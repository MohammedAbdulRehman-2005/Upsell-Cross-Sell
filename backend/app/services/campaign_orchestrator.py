"""Stage 5: Order-delivery event processing and campaign orchestration service.

Connects persisted orders to the approved Stage 4 recommendation engine with:
- Authoritative persisted order-state verification (only DELIVERED orders trigger campaigns)
- Fail-closed customer consent, opt-out, and WhatsApp destination checks
- Multi-layer idempotency (event_id uniqueness + (order_id, campaign_type) uniqueness + FOR UPDATE locking)
- Full recommendation provenance and decision-trace persistence
- Strict separation between campaign preparation (READY_FOR_DISPATCH / SUPPRESSED) and Stage 6 outbound dispatch
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.customer import Customer
from app.models.enums import (
    CampaignSuppressionReason,
    DeliveryEventOutcome,
    MessageDeliveryStatus,
    OrderStatus,
    WebhookEventType,
    WebhookProcessingStatus,
)
from app.models.message import MessageLog
from app.models.order import Order
from app.models.product import Product
from app.models.webhook import WebhookEvent
from app.repositories.message_repository import MessageLogRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.webhook_repository import WebhookEventRepository
from app.schemas.campaign import OrderDeliveredWebhookRequest, OrderDeliveryWebhookResponse
from app.services.recommendation import (
    DatabaseRecommendationService,
    OrderInput,
    OrderLineInput,
    RecommendationResult,
    RecommendationResultStatus,
)

logger = logging.getLogger(__name__)

WHATSAPP_PHONE_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")
SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "token",
        "access_token",
        "verify_token",
        "shared_token",
        "secret",
        "app_secret",
        "password",
        "authorization",
        "api_key",
    }
)
PHONE_PAYLOAD_KEYS = frozenset({"customer_phone", "phone_number", "recipient_phone", "phone"})

DEFAULT_CAMPAIGN_TYPE = "POST_DELIVERY_UPSELL"


class RetryableCampaignError(RuntimeError):
    """Raised when a transient error occurs during campaign orchestration that can be safely retried."""


class PermanentCampaignError(RuntimeError):
    """Raised when an unrecoverable data or domain error prevents campaign generation."""


@dataclass(frozen=True, slots=True)
class CustomerEligibilityDecision:
    """Result of evaluating a customer's WhatsApp messaging eligibility and consent."""

    is_eligible: bool
    suppression_reason: CampaignSuppressionReason | None
    normalized_phone: str | None
    detail: str


def mask_phone_number(phone: str | None) -> str:
    """Mask a phone number for privacy-safe logging and audit payloads."""
    if not phone or not str(phone).strip():
        return "<missing>"
    cleaned = str(phone).strip()
    if len(cleaned) <= 4:
        return "****"
    prefix = cleaned[:3] if cleaned.startswith("+") and len(cleaned) > 6 else cleaned[:2]
    suffix = cleaned[-4:]
    masked_middle_len = max(len(cleaned) - len(prefix) - len(suffix), 4)
    return f"{prefix}{'*' * masked_middle_len}{suffix}"


def sanitize_event_payload_for_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize an inbound event payload before persisting to webhook_events.payload.

    Preserves audit structure while masking customer phone numbers and redacting secrets.
    """

    def _sanitize_value(key: str | None, value: Any) -> Any:
        lower_key = (key or "").lower()
        if lower_key in SENSITIVE_PAYLOAD_KEYS or any(
            secret_word in lower_key for secret_word in ("secret", "password", "token", "api_key")
        ):
            return "***REDACTED***"
        if lower_key in PHONE_PAYLOAD_KEYS and isinstance(value, str):
            return mask_phone_number(value)
        if isinstance(value, dict):
            return {str(k): _sanitize_value(str(k), v) for k, v in value.items()}
        if isinstance(value, list):
            return [_sanitize_value(key, item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    return {str(k): _sanitize_value(str(k), v) for k, v in payload.items()}


def evaluate_customer_messaging_eligibility(
    customer: Customer | None,
    *,
    require_consent_audit_metadata: bool = False,
) -> CustomerEligibilityDecision:
    """Evaluate customer WhatsApp consent, opt-out status, and phone destination validity.

    Enforces fail-closed rules:
    1. Missing customer or missing/blank phone_number -> CUSTOMER_PHONE_MISSING
    2. Phone fails E.164 / WhatsApp destination format -> INVALID_WHATSAPP_DESTINATION
    3. whatsapp_opt_in is None or opted_out is None -> CONSENT_STATUS_UNKNOWN
    4. opted_out is True -> CUSTOMER_OPTED_OUT (always overrides whatsapp_opt_in=True)
    5. whatsapp_opt_in is False -> WHATSAPP_MARKETING_CONSENT_MISSING
    6. Optional strict consent provenance check -> CONSENT_STATUS_UNKNOWN
    """
    if customer is None:
        return CustomerEligibilityDecision(
            is_eligible=False,
            suppression_reason=CampaignSuppressionReason.CUSTOMER_PHONE_MISSING,
            normalized_phone=None,
            detail="Customer record is missing.",
        )

    raw_phone = customer.phone_number
    if raw_phone is None or not str(raw_phone).strip():
        return CustomerEligibilityDecision(
            is_eligible=False,
            suppression_reason=CampaignSuppressionReason.CUSTOMER_PHONE_MISSING,
            normalized_phone=None,
            detail="Customer phone number is missing or blank.",
        )

    cleaned_phone = str(raw_phone).strip()
    if not WHATSAPP_PHONE_REGEX.match(cleaned_phone):
        return CustomerEligibilityDecision(
            is_eligible=False,
            suppression_reason=CampaignSuppressionReason.INVALID_WHATSAPP_DESTINATION,
            normalized_phone=None,
            detail="Customer phone number is not a valid E.164 WhatsApp destination.",
        )

    if customer.whatsapp_opt_in is None or customer.opted_out is None:
        return CustomerEligibilityDecision(
            is_eligible=False,
            suppression_reason=CampaignSuppressionReason.CONSENT_STATUS_UNKNOWN,
            normalized_phone=cleaned_phone,
            detail="Customer consent or opt-out flag is NULL/unknown (fail-closed).",
        )

    if customer.opted_out is True:
        return CustomerEligibilityDecision(
            is_eligible=False,
            suppression_reason=CampaignSuppressionReason.CUSTOMER_OPTED_OUT,
            normalized_phone=cleaned_phone,
            detail="Customer has explicitly opted out of WhatsApp messages.",
        )

    if customer.whatsapp_opt_in is False:
        return CustomerEligibilityDecision(
            is_eligible=False,
            suppression_reason=CampaignSuppressionReason.WHATSAPP_MARKETING_CONSENT_MISSING,
            normalized_phone=cleaned_phone,
            detail="Customer has not opted in to WhatsApp marketing/upsell messages.",
        )

    if require_consent_audit_metadata:
        if (
            customer.consent_source is None
            or not str(customer.consent_source).strip()
            or customer.consent_recorded_at is None
        ):
            return CustomerEligibilityDecision(
                is_eligible=False,
                suppression_reason=CampaignSuppressionReason.CONSENT_STATUS_UNKNOWN,
                normalized_phone=cleaned_phone,
                detail="Required consent provenance metadata (consent_source / consent_recorded_at) is missing.",
            )

    return CustomerEligibilityDecision(
        is_eligible=True,
        suppression_reason=None,
        normalized_phone=cleaned_phone,
        detail="Customer is eligible for WhatsApp upsell campaign preparation.",
    )


class CampaignOrchestrationService:
    """Orchestrates Stage 5 order-delivery event processing and campaign persistence."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        recommendation_service: DatabaseRecommendationService | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.order_repo = OrderRepository(session)
        self.webhook_repo = WebhookEventRepository(session)
        self.message_repo = MessageLogRepository(session)
        self.recommendation_service = recommendation_service or DatabaseRecommendationService(session)

    def process_order_delivered_event(
        self,
        event: OrderDeliveredWebhookRequest,
        *,
        raw_payload: dict[str, Any] | None = None,
        campaign_type: str = DEFAULT_CAMPAIGN_TYPE,
    ) -> OrderDeliveryWebhookResponse:
        """Process an inbound order-delivery event end-to-end with transactional idempotency."""
        sanitized_payload = sanitize_event_payload_for_audit(
            raw_payload if raw_payload is not None else event.model_dump(mode="json")
        )

        # 1. Validate event type before state changes
        if event.event_type not in ("order.delivered", WebhookEventType.ORDER_DELIVERED.value):
            logger.warning(
                "Rejected delivery event with unsupported event_type: event_id=%s event_type=%s order_id=%s",
                event.event_id,
                event.event_type,
                event.order_id,
            )
            return OrderDeliveryWebhookResponse(
                event_id=event.event_id,
                event_type=event.event_type,
                order_id=event.order_id,
                outcome=DeliveryEventOutcome.REJECTED_UNSUPPORTED_EVENT_TYPE,
                idempotent_replay=False,
                campaign_type=campaign_type,
                detail=f"Unsupported event_type '{event.event_type}'. Expected 'order.delivered'.",
            )

        # 2. Validate authorized event source
        allowed_sources = {s.strip().lower() for s in self.settings.allowed_delivery_event_sources}
        if event.source.strip().lower() not in allowed_sources:
            logger.warning(
                "Rejected delivery event from unauthorized source: event_id=%s source=%s order_id=%s",
                event.event_id,
                event.source,
                event.order_id,
            )
            return OrderDeliveryWebhookResponse(
                event_id=event.event_id,
                event_type=event.event_type,
                order_id=event.order_id,
                outcome=DeliveryEventOutcome.REJECTED_UNAUTHORIZED_SOURCE,
                idempotent_replay=False,
                campaign_type=campaign_type,
                detail=f"Event source '{event.source}' is not an authorized delivery event source.",
            )

        # 3. Check if event_id was already recorded (idempotency layer 1 with FOR UPDATE lock)
        try:
            existing_webhook = self.webhook_repo.get_by_event_id_for_update(event.event_id)
            if existing_webhook is not None:
                if existing_webhook.processing_status in (
                    WebhookProcessingStatus.PROCESSED,
                    WebhookProcessingStatus.IGNORED,
                ):
                    return self._build_duplicate_event_response(
                        event=event,
                        campaign_type=campaign_type,
                        detail=f"Duplicate event_id '{event.event_id}' already processed; returning idempotent no-op.",
                    )
                # If previous attempt was FAILED or incomplete, re-attempt safely
                logger.info(
                    "Re-attempting previously failed webhook event: event_id=%s order_id=%s",
                    event.event_id,
                    event.order_id,
                )
                webhook_record = existing_webhook
                webhook_record.processing_status = WebhookProcessingStatus.RECEIVED
                webhook_record.error_message = None
                self.session.add(webhook_record)
                self.session.flush()
            else:
                # Insert WebhookEvent inside a savepoint so concurrent identical event_ids degrade gracefully
                try:
                    with self.session.begin_nested():
                        webhook_record = self.webhook_repo.create(
                            event_id=event.event_id,
                            event_type=WebhookEventType.ORDER_DELIVERED,
                            external_order_id=event.order_id,
                            source=event.source,
                            occurred_at=event.occurred_at,
                            payload=sanitized_payload,
                            processing_status=WebhookProcessingStatus.RECEIVED,
                            commit=False,
                        )
                except IntegrityError:
                    self.session.rollback()
                    logger.info(
                        "Concurrent duplicate event_id detected via unique constraint: event_id=%s order_id=%s",
                        event.event_id,
                        event.order_id,
                    )
                    return self._build_duplicate_event_response(
                        event=event,
                        campaign_type=campaign_type,
                        detail=f"Concurrent duplicate event_id '{event.event_id}' detected; returning idempotent no-op.",
                    )

            # 4. Lock and verify the persisted Order in PostgreSQL (SELECT ... FOR UPDATE)
            order = self.order_repo.get_by_external_id_for_update(event.order_id)
            if order is None:
                self._finalize_webhook(
                    webhook_record,
                    status=WebhookProcessingStatus.FAILED,
                    error_message=f"Persisted order '{event.order_id}' not found.",
                )
                self.session.commit()
                logger.warning(
                    "Delivery event references non-existent order: event_id=%s order_id=%s",
                    event.event_id,
                    event.order_id,
                )
                return OrderDeliveryWebhookResponse(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    order_id=event.order_id,
                    outcome=DeliveryEventOutcome.REJECTED_ORDER_NOT_FOUND,
                    idempotent_replay=False,
                    campaign_type=campaign_type,
                    detail=f"Order '{event.order_id}' does not exist in PostgreSQL.",
                )

            # 5. Verify customer association if customer_id was asserted in the event
            customer = order.customer
            if (
                event.customer_id is not None
                and customer is not None
                and customer.external_customer_id != event.customer_id
            ):
                self._finalize_webhook(
                    webhook_record,
                    status=WebhookProcessingStatus.FAILED,
                    error_message=(
                        f"Event customer_id '{event.customer_id}' mismatches persisted order customer "
                        f"'{customer.external_customer_id}'."
                    ),
                )
                self.session.commit()
                logger.warning(
                    "Order customer mismatch: event_id=%s order_id=%s event_customer=%s db_customer=%s",
                    event.event_id,
                    event.order_id,
                    event.customer_id,
                    customer.external_customer_id,
                )
                return OrderDeliveryWebhookResponse(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    order_id=event.order_id,
                    outcome=DeliveryEventOutcome.REJECTED_ORDER_CUSTOMER_MISMATCH,
                    idempotent_replay=False,
                    campaign_type=campaign_type,
                    detail="Event customer_id does not match persisted order customer.",
                )

            # 6. Authoritative persisted order-status gate: NEVER trust event payload status over DB state
            if order.order_status == OrderStatus.CANCELLED:
                self._finalize_webhook(
                    webhook_record,
                    status=WebhookProcessingStatus.IGNORED,
                    error_message="Ignored: persisted order status is CANCELLED.",
                )
                self.session.commit()
                logger.info(
                    "Ignored delivery event for CANCELLED order: event_id=%s order_id=%s",
                    event.event_id,
                    event.order_id,
                )
                return OrderDeliveryWebhookResponse(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    order_id=event.order_id,
                    outcome=DeliveryEventOutcome.REJECTED_ORDER_CANCELLED,
                    idempotent_replay=False,
                    campaign_type=campaign_type,
                    detail="Persisted order is CANCELLED; no campaign triggered.",
                )

            if order.order_status != OrderStatus.DELIVERED:
                order_status_str = str(order.order_status)
                self._finalize_webhook(
                    webhook_record,
                    status=WebhookProcessingStatus.IGNORED,
                    error_message=f"Ignored: persisted order status is '{order_status_str}', not 'DELIVERED'.",
                )
                self.session.commit()
                logger.info(
                    "Ignored delivery event for non-delivered order: event_id=%s order_id=%s db_status=%s",
                    event.event_id,
                    event.order_id,
                    order_status_str,
                )
                return OrderDeliveryWebhookResponse(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    order_id=event.order_id,
                    outcome=DeliveryEventOutcome.REJECTED_ORDER_NOT_DELIVERED,
                    idempotent_replay=False,
                    campaign_type=campaign_type,
                    detail=(
                        f"Persisted order status is '{order_status_str}' (must be 'DELIVERED'); "
                        "no campaign triggered."
                    ),
                )

            # 7. Check for existing campaign for (order.id, campaign_type) (idempotency layer 2)
            existing_campaign = self.message_repo.get_by_order_and_campaign(order.id, campaign_type)
            if existing_campaign is not None and existing_campaign.delivery_status != MessageDeliveryStatus.FAILED_RETRYABLE:
                existing_status_str = str(existing_campaign.delivery_status)
                self._finalize_webhook(
                    webhook_record,
                    status=WebhookProcessingStatus.IGNORED,
                    error_message=(
                        f"Campaign '{campaign_type}' already exists for order '{order.external_order_id}' "
                        f"(campaign_id={existing_campaign.id}, status={existing_status_str})."
                    ),
                )
                self.session.commit()
                logger.info(
                    "Duplicate order campaign prevented: event_id=%s order_id=%s existing_campaign_id=%s status=%s",
                    event.event_id,
                    event.order_id,
                    existing_campaign.id,
                    existing_status_str,
                )
                return self._build_response_from_campaign(
                    event=event,
                    campaign=existing_campaign,
                    outcome=DeliveryEventOutcome.DUPLICATE_ORDER_CAMPAIGN_NOOP,
                    idempotent_replay=True,
                    detail=(
                        f"Campaign '{campaign_type}' already exists for order '{event.order_id}'; "
                        "duplicate campaign skipped."
                    ),
                )

            # 8. Execute campaign eligibility & recommendation generation inside error-classified block
            try:
                return self._evaluate_and_persist_campaign(
                    event=event,
                    order=order,
                    customer=customer,
                    webhook_record=webhook_record,
                    existing_retryable_campaign=existing_campaign,
                    campaign_type=campaign_type,
                )
            except RetryableCampaignError as exc:
                return self._handle_retryable_failure(
                    event=event,
                    order=order,
                    customer=customer,
                    webhook_record=webhook_record,
                    existing_campaign=existing_campaign,
                    campaign_type=campaign_type,
                    error_detail=str(exc),
                )
            except PermanentCampaignError as exc:
                return self._handle_permanent_failure(
                    event=event,
                    order=order,
                    customer=customer,
                    webhook_record=webhook_record,
                    existing_campaign=existing_campaign,
                    campaign_type=campaign_type,
                    error_detail=str(exc),
                )
        except Exception as exc:
            self.session.rollback()
            logger.error(
                "Transaction rolled back due to unhandled failure during delivery event orchestration: "
                "event_id=%s order_id=%s error=%s",
                event.event_id,
                event.order_id,
                str(exc),
            )
            raise

    def _evaluate_and_persist_campaign(
        self,
        *,
        event: OrderDeliveredWebhookRequest,
        order: Order,
        customer: Customer | None,
        webhook_record: WebhookEvent,
        existing_retryable_campaign: MessageLog | None,
        campaign_type: str,
    ) -> OrderDeliveryWebhookResponse:
        """Evaluate customer consent, run Stage 4 recommendation engine, and persist the campaign record."""
        idempotency_key = f"campaign:{campaign_type}:order:{order.external_order_id}"

        # Step A: Evaluate customer consent, opt-out, and phone destination validity
        require_consent_audit = bool(self.settings.require_consent_audit_metadata) or (
            self.settings.environment in ("production", "staging")
        )
        eligibility = evaluate_customer_messaging_eligibility(
            customer,
            require_consent_audit_metadata=require_consent_audit,
        )
        if not eligibility.is_eligible:
            assert eligibility.suppression_reason is not None
            suppression_trace = {
                "stage": "CUSTOMER_CONSENT_AND_DESTINATION_GATE",
                "order_id": order.external_order_id,
                "customer_id": customer.external_customer_id if customer else None,
                "masked_phone": mask_phone_number(customer.phone_number if customer else None),
                "whatsapp_opt_in": customer.whatsapp_opt_in if customer else None,
                "opted_out": customer.opted_out if customer else None,
                "consent_source": customer.consent_source if customer else None,
                "suppression_reason": eligibility.suppression_reason.value,
                "detail": eligibility.detail,
            }
            campaign = self._upsert_campaign_record(
                existing_record=existing_retryable_campaign,
                idempotency_key=idempotency_key,
                order_id=order.id,
                customer_id=order.customer_id,
                recipient_phone=eligibility.normalized_phone,
                campaign_type=campaign_type,
                trigger_event_id=event.event_id,
                delivery_status=MessageDeliveryStatus.SUPPRESSED,
                recommendation_status=None,
                suppression_reason=eligibility.suppression_reason.value,
                recommended_skus=[],
                decision_trace=suppression_trace,
                metadata_provenance=None,
                request_payload=None,
                error_detail=eligibility.detail,
            )
            self._finalize_webhook(
                webhook_record,
                status=WebhookProcessingStatus.PROCESSED,
                error_message=None,
            )
            self.session.commit()
            self.session.refresh(campaign)
            logger.info(
                "Campaign suppressed at customer eligibility gate: event_id=%s order_id=%s customer_id=%s phone=%s reason=%s",
                event.event_id,
                order.external_order_id,
                customer.external_customer_id if customer else "<none>",
                mask_phone_number(customer.phone_number if customer else None),
                eligibility.suppression_reason.value,
            )
            return self._build_response_from_campaign(
                event=event,
                campaign=campaign,
                outcome=DeliveryEventOutcome.CAMPAIGN_SUPPRESSED,
                idempotent_replay=False,
                detail=eligibility.detail,
            )

        # Step B: Validate persisted order line items before invoking Stage 4 recommendation engine
        sorted_items = sorted(order.line_items, key=lambda item: item.line_index)
        if not sorted_items:
            raise PermanentCampaignError(
                f"Persisted order '{order.external_order_id}' has no line items."
            )
        for item in sorted_items:
            if not item.product_code or not str(item.product_code).strip() or item.quantity <= 0:
                raise PermanentCampaignError(
                    f"Persisted order '{order.external_order_id}' contains invalid line item "
                    f"(line_index={item.line_index}, product_code={item.product_code!r}, quantity={item.quantity})."
                )

        order_input = OrderInput(
            order_id=order.external_order_id,
            line_items=tuple(
                OrderLineInput(
                    product_code=item.product_code,
                    quantity=item.quantity,
                    line_index=item.line_index,
                    product_name=item.product_name,
                )
                for item in sorted_items
            ),
        )

        # Step C: Invoke Stage 4 deterministic recommendation engine
        rec_result = self.recommendation_service.generate_for_order_input(order_input)
        serialized_rec = rec_result.to_dict()
        recommended_skus = list(rec_result.trace.ordered_recommended_skus)
        provenance_summary = self._collect_metadata_provenance(rec_result)

        # Step D: Map recommendation outcome to campaign state
        if rec_result.status in (
            RecommendationResultStatus.NO_MAPPED_SOURCE_PRODUCTS,
            RecommendationResultStatus.NO_ELIGIBLE_RECOMMENDATIONS,
        ):
            suppression_reason = (
                CampaignSuppressionReason.NO_MAPPED_SOURCE_PRODUCTS
                if rec_result.status == RecommendationResultStatus.NO_MAPPED_SOURCE_PRODUCTS
                else CampaignSuppressionReason.NO_ELIGIBLE_RECOMMENDATIONS
            )
            detail_msg = (
                f"Campaign suppressed: recommendation engine returned '{rec_result.status.value}' "
                f"for order '{order.external_order_id}'."
            )
            campaign = self._upsert_campaign_record(
                existing_record=existing_retryable_campaign,
                idempotency_key=idempotency_key,
                order_id=order.id,
                customer_id=order.customer_id,
                recipient_phone=eligibility.normalized_phone,
                campaign_type=campaign_type,
                trigger_event_id=event.event_id,
                delivery_status=MessageDeliveryStatus.SUPPRESSED,
                recommendation_status=rec_result.status.value,
                suppression_reason=suppression_reason.value,
                recommended_skus=[],
                decision_trace=serialized_rec["trace"],
                metadata_provenance=provenance_summary,
                request_payload=None,
                error_detail=detail_msg,
            )
            self._finalize_webhook(
                webhook_record,
                status=WebhookProcessingStatus.PROCESSED,
                error_message=None,
            )
            self.session.commit()
            self.session.refresh(campaign)
            logger.info(
                "Campaign suppressed due to zero recommendations: event_id=%s order_id=%s rec_status=%s reason=%s",
                event.event_id,
                order.external_order_id,
                rec_result.status.value,
                suppression_reason.value,
            )
            return self._build_response_from_campaign(
                event=event,
                campaign=campaign,
                outcome=DeliveryEventOutcome.CAMPAIGN_SUPPRESSED,
                idempotent_replay=False,
                detail=detail_msg,
            )

        if rec_result.status in (
            RecommendationResultStatus.SUCCESS,
            RecommendationResultStatus.PARTIAL_SUCCESS,
        ):
            prepared_dispatch_payload = {
                "campaign_type": campaign_type,
                "order_id": order.external_order_id,
                "customer_id": customer.external_customer_id if customer else str(order.customer_id),
                "masked_recipient_phone": mask_phone_number(eligibility.normalized_phone),
                "recommendation_status": rec_result.status.value,
                "recommended_count": len(rec_result.recommendations),
                "recommendations": serialized_rec["recommendations"],
                "metadata_provenance": provenance_summary,
                "stage": "STAGE_5_PREPARED_NOT_DISPATCHED",
            }
            detail_msg = (
                f"Campaign prepared and marked READY_FOR_DISPATCH with {len(recommended_skus)} "
                f"recommendations (recommendation_status={rec_result.status.value})."
            )
            campaign = self._upsert_campaign_record(
                existing_record=existing_retryable_campaign,
                idempotency_key=idempotency_key,
                order_id=order.id,
                customer_id=order.customer_id,
                recipient_phone=eligibility.normalized_phone,
                campaign_type=campaign_type,
                trigger_event_id=event.event_id,
                delivery_status=MessageDeliveryStatus.READY_FOR_DISPATCH,
                recommendation_status=rec_result.status.value,
                suppression_reason=None,
                recommended_skus=recommended_skus,
                decision_trace=serialized_rec["trace"],
                metadata_provenance=provenance_summary,
                request_payload=prepared_dispatch_payload,
                error_detail=None,
            )
            self._finalize_webhook(
                webhook_record,
                status=WebhookProcessingStatus.PROCESSED,
                error_message=None,
            )
            self.session.commit()
            self.session.refresh(campaign)
            logger.info(
                "Campaign READY_FOR_DISPATCH: event_id=%s order_id=%s campaign_id=%s rec_status=%s skus=%s",
                event.event_id,
                order.external_order_id,
                campaign.id,
                rec_result.status.value,
                recommended_skus,
            )
            return self._build_response_from_campaign(
                event=event,
                campaign=campaign,
                outcome=DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH,
                idempotent_replay=False,
                detail=detail_msg,
            )

        raise PermanentCampaignError(
            f"Unexpected recommendation engine status '{rec_result.status.value}' for order '{order.external_order_id}'."
        )

    def _upsert_campaign_record(
        self,
        *,
        existing_record: MessageLog | None,
        idempotency_key: str,
        order_id: int,
        customer_id: int,
        recipient_phone: str | None,
        campaign_type: str,
        trigger_event_id: str,
        delivery_status: MessageDeliveryStatus,
        recommendation_status: str | None,
        suppression_reason: str | None,
        recommended_skus: list[str],
        decision_trace: dict[str, Any] | None,
        metadata_provenance: dict[str, Any] | None,
        request_payload: dict[str, Any] | None,
        error_detail: str | None,
    ) -> MessageLog:
        """Create a new MessageLog or update an existing FAILED_RETRYABLE MessageLog in-place."""
        if existing_record is not None:
            existing_record.trigger_event_id = trigger_event_id
            existing_record.recipient_phone = recipient_phone
            existing_record.delivery_status = delivery_status
            existing_record.recommendation_status = recommendation_status
            existing_record.suppression_reason = suppression_reason
            existing_record.recommended_skus = recommended_skus
            existing_record.decision_trace = decision_trace
            existing_record.metadata_provenance = metadata_provenance
            existing_record.request_payload = request_payload
            existing_record.error_detail = error_detail
            existing_record.retry_count = existing_record.retry_count + 1
            self.session.add(existing_record)
            self.session.flush()
            return existing_record

        try:
            with self.session.begin_nested():
                return self.message_repo.create(
                    idempotency_key=idempotency_key,
                    order_id=order_id,
                    customer_id=customer_id,
                    recipient_phone=recipient_phone,
                    provider=None,
                    campaign_type=campaign_type,
                    trigger_event_id=trigger_event_id,
                    delivery_status=delivery_status,
                    recommendation_status=recommendation_status,
                    suppression_reason=suppression_reason,
                    recommended_skus=recommended_skus,
                    decision_trace=decision_trace,
                    metadata_provenance=metadata_provenance,
                    request_payload=request_payload,
                    error_detail=error_detail,
                    commit=False,
                )
        except IntegrityError as exc:
            # Another concurrent worker created the campaign for this (order_id, campaign_type)
            logger.info(
                "Unique constraint on (order_id=%s, campaign_type=%s) triggered; loading existing campaign.",
                order_id,
                campaign_type,
            )
            existing = self.message_repo.get_by_order_and_campaign(order_id, campaign_type)
            if existing is not None:
                return existing
            raise exc

    def _collect_metadata_provenance(self, rec_result: RecommendationResult) -> dict[str, Any]:
        """Summarize catalogue metadata provenance across evaluated candidate SKUs and selected recommendations."""
        candidate_codes: set[str] = set()
        for src in rec_result.trace.source_evaluations:
            candidate_codes.add(src.product_code)
        for cand in rec_result.trace.candidate_evaluations:
            candidate_codes.add(cand.candidate_product_code)

        selected_codes = list(rec_result.trace.ordered_recommended_skus)
        all_codes = sorted(candidate_codes.union(selected_codes))
        if not all_codes:
            return {
                "evaluated_product_count": 0,
                "selected_product_count": 0,
                "source_counts": {},
                "selected_source_counts": {},
                "contains_synthetic_metadata": False,
                "contains_workbook_only_metadata": False,
            }

        stmt = select(
            Product.product_code,
            Product.metadata_source,
            Product.is_synthetic_metadata,
        ).where(Product.product_code.in_(all_codes))
        rows = self.session.execute(stmt).all()

        source_counts: dict[str, int] = {}
        selected_source_counts: dict[str, int] = {}
        selected_set = set(selected_codes)
        any_synthetic = False
        any_workbook_only = False

        for code, meta_src, is_synth in rows:
            src_str = str(meta_src)
            source_counts[src_str] = source_counts.get(src_str, 0) + 1
            if is_synth or src_str == "SYNTHETIC_FIXTURE":
                any_synthetic = True
            if src_str == "WORKBOOK_ONLY":
                any_workbook_only = True
            if code in selected_set:
                selected_source_counts[src_str] = selected_source_counts.get(src_str, 0) + 1

        return {
            "evaluated_product_count": len(rows),
            "selected_product_count": len(selected_codes),
            "source_counts": source_counts,
            "selected_source_counts": selected_source_counts,
            "contains_synthetic_metadata": any_synthetic,
            "contains_workbook_only_metadata": any_workbook_only,
        }

    def _handle_retryable_failure(
        self,
        *,
        event: OrderDeliveredWebhookRequest,
        order: Order,
        customer: Customer | None,
        webhook_record: WebhookEvent,
        existing_campaign: MessageLog | None,
        campaign_type: str,
        error_detail: str,
    ) -> OrderDeliveryWebhookResponse:
        idempotency_key = f"campaign:{campaign_type}:order:{order.external_order_id}"
        campaign = self._upsert_campaign_record(
            existing_record=existing_campaign,
            idempotency_key=idempotency_key,
            order_id=order.id,
            customer_id=order.customer_id,
            recipient_phone=customer.phone_number if customer else None,
            campaign_type=campaign_type,
            trigger_event_id=event.event_id,
            delivery_status=MessageDeliveryStatus.FAILED_RETRYABLE,
            recommendation_status=None,
            suppression_reason=None,
            recommended_skus=[],
            decision_trace={"error_type": "RETRYABLE", "error_detail": error_detail},
            metadata_provenance=None,
            request_payload=None,
            error_detail=error_detail,
        )
        self._finalize_webhook(
            webhook_record,
            status=WebhookProcessingStatus.FAILED,
            error_message=error_detail,
        )
        self.session.commit()
        self.session.refresh(campaign)
        logger.warning(
            "Campaign marked FAILED_RETRYABLE: event_id=%s order_id=%s campaign_id=%s error=%s",
            event.event_id,
            order.external_order_id,
            campaign.id,
            error_detail,
        )
        return self._build_response_from_campaign(
            event=event,
            campaign=campaign,
            outcome=DeliveryEventOutcome.FAILED_RETRYABLE,
            idempotent_replay=False,
            detail=error_detail,
        )

    def _handle_permanent_failure(
        self,
        *,
        event: OrderDeliveredWebhookRequest,
        order: Order,
        customer: Customer | None,
        webhook_record: WebhookEvent,
        existing_campaign: MessageLog | None,
        campaign_type: str,
        error_detail: str,
    ) -> OrderDeliveryWebhookResponse:
        idempotency_key = f"campaign:{campaign_type}:order:{order.external_order_id}"
        campaign = self._upsert_campaign_record(
            existing_record=existing_campaign,
            idempotency_key=idempotency_key,
            order_id=order.id,
            customer_id=order.customer_id,
            recipient_phone=customer.phone_number if customer else None,
            campaign_type=campaign_type,
            trigger_event_id=event.event_id,
            delivery_status=MessageDeliveryStatus.FAILED_PERMANENT,
            recommendation_status=None,
            suppression_reason=CampaignSuppressionReason.INVALID_PERSISTED_ORDER_DATA.value,
            recommended_skus=[],
            decision_trace={"error_type": "PERMANENT", "error_detail": error_detail},
            metadata_provenance=None,
            request_payload=None,
            error_detail=error_detail,
        )
        self._finalize_webhook(
            webhook_record,
            status=WebhookProcessingStatus.FAILED,
            error_message=error_detail,
        )
        self.session.commit()
        self.session.refresh(campaign)
        logger.error(
            "Campaign marked FAILED_PERMANENT: event_id=%s order_id=%s campaign_id=%s error=%s",
            event.event_id,
            order.external_order_id,
            campaign.id,
            error_detail,
        )
        return self._build_response_from_campaign(
            event=event,
            campaign=campaign,
            outcome=DeliveryEventOutcome.FAILED_PERMANENT,
            idempotent_replay=False,
            detail=error_detail,
        )

    def _finalize_webhook(
        self,
        record: WebhookEvent,
        *,
        status: WebhookProcessingStatus,
        error_message: str | None,
    ) -> None:
        record.processing_status = status
        record.error_message = error_message
        record.processed_at = datetime.now(timezone.utc)
        self.session.add(record)
        self.session.flush()

    def _build_duplicate_event_response(
        self,
        *,
        event: OrderDeliveredWebhookRequest,
        campaign_type: str,
        detail: str,
    ) -> OrderDeliveryWebhookResponse:
        order = self.order_repo.get_by_external_id(event.order_id)
        existing_campaign = (
            self.message_repo.get_by_order_and_campaign(order.id, campaign_type)
            if order is not None
            else None
        )
        if existing_campaign is not None:
            return self._build_response_from_campaign(
                event=event,
                campaign=existing_campaign,
                outcome=DeliveryEventOutcome.DUPLICATE_EVENT_NOOP,
                idempotent_replay=True,
                detail=detail,
            )
        return OrderDeliveryWebhookResponse(
            event_id=event.event_id,
            event_type=event.event_type,
            order_id=event.order_id,
            outcome=DeliveryEventOutcome.DUPLICATE_EVENT_NOOP,
            idempotent_replay=True,
            campaign_type=campaign_type,
            detail=detail,
        )

    @staticmethod
    def _build_response_from_campaign(
        *,
        event: OrderDeliveredWebhookRequest,
        campaign: MessageLog,
        outcome: DeliveryEventOutcome,
        idempotent_replay: bool,
        detail: str,
    ) -> OrderDeliveryWebhookResponse:
        return OrderDeliveryWebhookResponse(
            event_id=event.event_id,
            event_type=event.event_type,
            order_id=event.order_id,
            outcome=outcome,
            idempotent_replay=idempotent_replay,
            campaign_id=campaign.id,
            campaign_type=campaign.campaign_type,
            campaign_status=campaign.delivery_status,
            recommendation_status=campaign.recommendation_status,
            suppression_reason=campaign.suppression_reason,
            recommended_skus=list(campaign.recommended_skus or []),
            metadata_provenance=campaign.metadata_provenance,
            detail=detail,
        )
