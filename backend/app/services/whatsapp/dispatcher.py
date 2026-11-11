"""Dispatch coordinator for persisted post-delivery upsell campaigns."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.enums import (
    MessageDeliveryStatus,
    MessagingProvider,
    SimulatorMessageDirection,
)
from app.models.message import MessageLog
from app.repositories.customer_repository import CustomerRepository
from app.repositories.message_repository import MessageLogRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.repositories.simulator_repository import SimulatorMessageRepository
from app.schemas.whatsapp import (
    CampaignDetailSchema,
    CampaignDispatchResponse,
    CampaignSummarySchema,
    RecommendationCardSchema,
    SimulatorMessageSchema,
)
from app.services.whatsapp.models import (
    OutboundTemplateMessageRequest,
    RecommendationCardItem,
)
from app.services.whatsapp.providers import (
    STRICT_E164_REGEX,
    WhatsAppProvider,
    get_whatsapp_provider,
)

ALREADY_DISPATCHED_STATUSES: frozenset[str] = frozenset(
    {
        MessageDeliveryStatus.DISPATCHING.value,
        MessageDeliveryStatus.PROVIDER_ACCEPTED.value,
        MessageDeliveryStatus.SENT_SIMULATED.value,
        MessageDeliveryStatus.DELIVERY_PENDING.value,
        MessageDeliveryStatus.DELIVERED.value,
        MessageDeliveryStatus.READ.value,
    }
)

NON_DISPATCHABLE_STATUSES: frozenset[str] = frozenset(
    {
        MessageDeliveryStatus.SUPPRESSED.value,
        MessageDeliveryStatus.FAILED_PERMANENT.value,
        MessageDeliveryStatus.DELIVERY_UNKNOWN.value,
    }
)


def build_recommendation_cards(
    session: Session,
    campaign: MessageLog,
) -> list[RecommendationCardItem]:
    """Hydrate ordered RecommendationCardItem objects from campaign SKUs, catalogue, and trace."""
    skus = list(campaign.recommended_skus or [])
    if not skus:
        return []

    product_repo = ProductRepository(session)
    products_by_sku = {p.product_code: p for p in product_repo.get_by_codes(skus)}

    # Extract slot metadata from decision_trace if present
    trace_by_sku: dict[str, dict[str, Any]] = {}
    if isinstance(campaign.decision_trace, dict):
        selected_recs = campaign.decision_trace.get("selection_provenance")
        if isinstance(selected_recs, list):
            for rec in selected_recs:
                if isinstance(rec, dict) and rec.get("recommended_product_code"):
                    trace_by_sku[str(rec["recommended_product_code"])] = rec

    cards: list[RecommendationCardItem] = []
    for idx, sku in enumerate(skus, start=1):
        product = products_by_sku.get(sku)
        trace_item = trace_by_sku.get(sku, {})
        product_name = (
            product.product_name
            if product is not None
            else str(trace_item.get("recommended_product_name") or sku)
        )
        category = None
        unit_price = (
            float(product.unit_price)
            if (product is not None and product.unit_price is not None)
            else None
        )
        metadata_source = (
            str(product.metadata_source)
            if (product is not None and product.metadata_source is not None)
            else None
        )
        source_sku = trace_item.get("source_product_code")
        source_rank = trace_item.get("original_mapping_rank")
        slot_num = int(trace_item.get("selection_sequence") or idx)

        cards.append(
            RecommendationCardItem(
                slot_number=slot_num,
                product_code=sku,
                product_name=product_name,
                category=category,
                unit_price=unit_price,
                source_product_code=str(source_sku) if source_sku else None,
                source_rank=int(source_rank) if source_rank is not None else None,
                metadata_source=metadata_source,
            )
        )
    return cards


def serialize_campaign_summary(
    session: Session,
    campaign: MessageLog,
) -> CampaignSummarySchema:
    """Convert a MessageLog ORM entity into a CampaignSummarySchema."""
    order_repo = OrderRepository(session)
    customer_repo = CustomerRepository(session)
    order = order_repo.get_by_id(campaign.order_id)
    customer = customer_repo.get_by_id(campaign.customer_id)

    status_str = str(campaign.delivery_status)
    provider_str = str(campaign.provider) if campaign.provider else None
    is_sim = (
        status_str == MessageDeliveryStatus.SENT_SIMULATED.value
        or provider_str == MessagingProvider.WHATSAPP_SIMULATOR.value
    )

    return CampaignSummarySchema(
        campaign_id=campaign.id,
        idempotency_key=campaign.idempotency_key,
        order_id=campaign.order_id,
        external_order_id=order.external_order_id if order else f"order-{campaign.order_id}",
        order_number=None,
        customer_id=campaign.customer_id,
        external_customer_id=(
            customer.external_customer_id if customer else f"customer-{campaign.customer_id}"
        ),
        recipient_phone=campaign.recipient_phone,
        provider=provider_str,
        delivery_status=status_str,
        recommendation_status=campaign.recommendation_status,
        suppression_reason=campaign.suppression_reason,
        recommended_skus=list(campaign.recommended_skus or []),
        provider_message_id=campaign.provider_message_id,
        provider_error_code=campaign.provider_error_code,
        provider_error_message=campaign.provider_error_message,
        retry_count=campaign.retry_count,
        is_simulated=is_sim,
        dispatch_started_at=campaign.dispatch_started_at,
        provider_accepted_at=campaign.provider_accepted_at,
        delivered_at=campaign.delivered_at,
        read_at=campaign.read_at,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def serialize_campaign_detail(
    session: Session,
    campaign: MessageLog,
) -> CampaignDetailSchema:
    """Convert a MessageLog ORM entity into a full CampaignDetailSchema with cards and chat history."""
    summary = serialize_campaign_summary(session, campaign)
    order_repo = OrderRepository(session)
    sim_repo = SimulatorMessageRepository(session)
    product_repo = ProductRepository(session)

    order = order_repo.get_by_id(campaign.order_id)
    source_items: list[dict[str, Any]] = []
    if order and order.line_items:
        source_skus = [item.product_code for item in order.line_items]
        products_map = {p.product_code: p for p in product_repo.get_by_codes(source_skus)}
        for item in order.line_items:
            prod = products_map.get(item.product_code)
            source_items.append(
                {
                    "sku_code": item.product_code,
                    "product_name": item.product_name or (prod.product_name if prod else item.product_code),
                    "quantity": item.quantity,
                }
            )

    cards = [
        RecommendationCardSchema(**card.to_dict())
        for card in build_recommendation_cards(session, campaign)
    ]
    sim_messages = [
        SimulatorMessageSchema.model_validate(msg)
        for msg in sim_repo.list_by_message_log_id(campaign.id)
    ]

    return CampaignDetailSchema(
        **summary.model_dump(),
        source_items=source_items,
        recommendation_cards=cards,
        decision_trace=campaign.decision_trace,
        metadata_provenance=campaign.metadata_provenance,
        request_payload=campaign.request_payload,
        response_payload=campaign.response_payload,
        error_detail=campaign.error_detail,
        simulator_messages=sim_messages,
    )


class DispatchCoordinator:
    """
    Coordinates outbound WhatsApp dispatch for persisted campaigns.

    Key architectural guarantees:
    1. Only campaigns in `READY_FOR_DISPATCH` (or `FAILED_RETRYABLE` when `force_retry=True`)
       are dispatched.
    2. `SUPPRESSED`, `FAILED_PERMANENT`, and `DELIVERY_UNKNOWN` campaigns are never dispatched.
    3. Already-dispatched or currently `DISPATCHING` campaigns return an idempotent no-op.
    4. Short Transaction 1 transitions `READY_FOR_DISPATCH -> DISPATCHING` and commits BEFORE
       making any external network call, so no DB row lock or transaction is held across HTTP.
    5. Short Transaction 2 records the final provider outcome (`SENT_SIMULATED`,
       `PROVIDER_ACCEPTED`, `FAILED_RETRYABLE`, `FAILED_PERMANENT`, or `DELIVERY_UNKNOWN`).
    """

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        provider: WhatsAppProvider | None = None,
        before_provider_send_hook: Callable[[int], None] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.provider = provider or get_whatsapp_provider(settings=self.settings)
        self.before_provider_send_hook = before_provider_send_hook

    def dispatch_campaign(
        self,
        campaign_id: int,
        *,
        force_retry: bool = False,
    ) -> CampaignDispatchResponse:
        msg_repo = MessageLogRepository(self.session)
        order_repo = OrderRepository(self.session)
        customer_repo = CustomerRepository(self.session)
        sim_repo = SimulatorMessageRepository(self.session)

        # ------------------------------------------------------------------
        # PHASE 1: Short DB Transaction â€” Lock row, verify eligibility, mark DISPATCHING, commit
        # ------------------------------------------------------------------
        campaign = msg_repo.get_by_id_for_update(campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign {campaign_id} not found.")

        previous_status = str(campaign.delivery_status)
        order = order_repo.get_by_id(campaign.order_id)
        customer = customer_repo.get_by_id(campaign.customer_id)
        external_order_id = order.external_order_id if order else f"order-{campaign.order_id}"
        order_number = None
        external_customer_id = (
            customer.external_customer_id if customer else f"customer-{campaign.customer_id}"
        )

        # 1. Check if already dispatched or currently dispatching (idempotent no-op)
        if previous_status in ALREADY_DISPATCHED_STATUSES:
            self.session.rollback()
            existing_sim_msgs = sim_repo.list_by_message_log_id(campaign_id)
            first_sim = (
                SimulatorMessageSchema.model_validate(existing_sim_msgs[0])
                if existing_sim_msgs
                else None
            )
            return CampaignDispatchResponse(
                campaign_id=campaign.id,
                order_id=campaign.order_id,
                external_order_id=external_order_id,
                provider=str(campaign.provider or self.provider.provider_type.value),
                previous_status=previous_status,
                delivery_status=previous_status,
                dispatch_action="NO_OP_ALREADY_DISPATCHED",
                provider_message_id=campaign.provider_message_id,
                provider_error_code=campaign.provider_error_code,
                provider_error_message=campaign.provider_error_message,
                is_simulated=(
                    previous_status == MessageDeliveryStatus.SENT_SIMULATED.value
                    or str(campaign.provider) == MessagingProvider.WHATSAPP_SIMULATOR.value
                ),
                retry_count=campaign.retry_count,
                simulated_message=first_sim,
                detail=(
                    f"Campaign {campaign.id} is already in status '{previous_status}'; "
                    "duplicate dispatch prevented."
                ),
            )

        # 2. Check if campaign is suppressed, permanently failed, or in ambiguous delivery state
        if previous_status in NON_DISPATCHABLE_STATUSES:
            self.session.rollback()
            return CampaignDispatchResponse(
                campaign_id=campaign.id,
                order_id=campaign.order_id,
                external_order_id=external_order_id,
                provider=str(campaign.provider or self.provider.provider_type.value),
                previous_status=previous_status,
                delivery_status=previous_status,
                dispatch_action="REJECTED_NON_DISPATCHABLE_STATUS",
                provider_message_id=campaign.provider_message_id,
                provider_error_code=campaign.provider_error_code,
                provider_error_message=campaign.provider_error_message,
                is_simulated=False,
                retry_count=campaign.retry_count,
                detail=(
                    f"Campaign {campaign.id} is in status '{previous_status}' "
                    f"(suppression_reason={campaign.suppression_reason}) and cannot be dispatched."
                ),
            )

        # 3. Check if campaign is FAILED_RETRYABLE without force_retry
        if previous_status == MessageDeliveryStatus.FAILED_RETRYABLE.value and not force_retry:
            self.session.rollback()
            return CampaignDispatchResponse(
                campaign_id=campaign.id,
                order_id=campaign.order_id,
                external_order_id=external_order_id,
                provider=str(campaign.provider or self.provider.provider_type.value),
                previous_status=previous_status,
                delivery_status=previous_status,
                dispatch_action="REJECTED_RETRY_REQUIRES_EXPLICIT_FLAG",
                provider_message_id=campaign.provider_message_id,
                provider_error_code=campaign.provider_error_code,
                provider_error_message=campaign.provider_error_message,
                is_simulated=False,
                retry_count=campaign.retry_count,
                detail=(
                    f"Campaign {campaign.id} is in FAILED_RETRYABLE status; "
                    "pass force_retry=True to retry dispatch."
                ),
            )

        # 4. Enforce that status is READY_FOR_DISPATCH (or FAILED_RETRYABLE with force_retry=True)
        if previous_status not in {
            MessageDeliveryStatus.READY_FOR_DISPATCH.value,
            MessageDeliveryStatus.FAILED_RETRYABLE.value,
        }:
            self.session.rollback()
            return CampaignDispatchResponse(
                campaign_id=campaign.id,
                order_id=campaign.order_id,
                external_order_id=external_order_id,
                provider=str(campaign.provider or self.provider.provider_type.value),
                previous_status=previous_status,
                delivery_status=previous_status,
                dispatch_action="REJECTED_INVALID_STATUS",
                is_simulated=False,
                retry_count=campaign.retry_count,
                detail=f"Campaign {campaign.id} has status '{previous_status}' instead of READY_FOR_DISPATCH.",
            )

        # 5. Validate campaign payload invariants (non-empty recommendations & strict E.164 phone)
        recipient_phone = (campaign.recipient_phone or "").strip()
        if not STRICT_E164_REGEX.match(recipient_phone):
            msg_repo.update_delivery_state(
                campaign,
                delivery_status=MessageDeliveryStatus.FAILED_PERMANENT,
                provider=self.provider.provider_type,
                provider_error_code="INVALID_RECIPIENT_PHONE",
                provider_error_message="Campaign recipient_phone failed strict E.164 validation.",
                error_detail="Campaign recipient_phone failed strict E.164 validation.",
                commit=True,
            )
            return CampaignDispatchResponse(
                campaign_id=campaign.id,
                order_id=campaign.order_id,
                external_order_id=external_order_id,
                provider=self.provider.provider_type.value,
                previous_status=previous_status,
                delivery_status=MessageDeliveryStatus.FAILED_PERMANENT.value,
                dispatch_action="FAILED_VALIDATION",
                provider_error_code="INVALID_RECIPIENT_PHONE",
                provider_error_message="Campaign recipient_phone failed strict E.164 validation.",
                retry_count=campaign.retry_count,
                detail="Campaign recipient_phone failed strict E.164 validation.",
            )

        cards = build_recommendation_cards(self.session, campaign)
        if not cards:
            msg_repo.update_delivery_state(
                campaign,
                delivery_status=MessageDeliveryStatus.FAILED_PERMANENT,
                provider=self.provider.provider_type,
                provider_error_code="EMPTY_RECOMMENDATIONS",
                provider_error_message="Campaign has no recommended SKUs to dispatch.",
                error_detail="Campaign has no recommended SKUs to dispatch.",
                commit=True,
            )
            return CampaignDispatchResponse(
                campaign_id=campaign.id,
                order_id=campaign.order_id,
                external_order_id=external_order_id,
                provider=self.provider.provider_type.value,
                previous_status=previous_status,
                delivery_status=MessageDeliveryStatus.FAILED_PERMANENT.value,
                dispatch_action="FAILED_VALIDATION",
                provider_error_code="EMPTY_RECOMMENDATIONS",
                provider_error_message="Campaign has no recommended SKUs to dispatch.",
                retry_count=campaign.retry_count,
                detail="Campaign has no recommended SKUs to dispatch.",
            )

        # Build outbound request before committing Phase 1
        outbound_request = OutboundTemplateMessageRequest(
            campaign_id=campaign.id,
            idempotency_key=campaign.idempotency_key,
            order_id=campaign.order_id,
            external_order_id=external_order_id,
            order_number=order_number,
            customer_id=campaign.customer_id,
            external_customer_id=external_customer_id,
            recipient_phone=recipient_phone,
            template_name=self.settings.whatsapp_template_name,
            template_language=self.settings.whatsapp_template_language,
            recommendations=cards,
        )

        new_retry_count = (
            campaign.retry_count + 1
            if previous_status == MessageDeliveryStatus.FAILED_RETRYABLE.value
            else campaign.retry_count
        )
        now_start = datetime.now(timezone.utc)

        # Mark DISPATCHING and COMMIT immediately so the DB transaction and row lock are released!
        msg_repo.update_delivery_state(
            campaign,
            delivery_status=MessageDeliveryStatus.DISPATCHING,
            provider=self.provider.provider_type,
            retry_count=new_retry_count,
            dispatch_started_at=now_start,
            clear_errors=True,
            commit=True,
        )

        # Optional test hook to verify that no DB lock is held during provider execution
        if self.before_provider_send_hook is not None:
            self.before_provider_send_hook(campaign_id)

        # ------------------------------------------------------------------
        # PHASE 2: Execute Provider Call Outside Database Transaction
        # ------------------------------------------------------------------
        send_result = self.provider.send_template_message(outbound_request)

        # ------------------------------------------------------------------
        # PHASE 3: Short DB Transaction â€” Record Provider Outcome
        # ------------------------------------------------------------------
        locked_campaign = msg_repo.get_by_id_for_update(campaign_id)
        if locked_campaign is None:
            raise RuntimeError(f"Campaign {campaign_id} disappeared during dispatch.")

        simulated_msg_schema: SimulatorMessageSchema | None = None
        if (
            send_result.is_simulated
            and send_result.delivery_status == MessageDeliveryStatus.SENT_SIMULATED
            and send_result.provider_message_id
        ):
            sim_record = sim_repo.create(
                simulated_message_id=send_result.provider_message_id,
                message_log_id=locked_campaign.id,
                order_id=locked_campaign.order_id,
                customer_id=locked_campaign.customer_id,
                recipient_phone=recipient_phone,
                direction=SimulatorMessageDirection.OUTBOUND,
                message_type="TEMPLATE_RECOMMENDATION",
                body_text=send_result.rendered_body_text or "",
                recommendations_payload=[c.to_dict() for c in cards],
                metadata_json={'template_name': self.settings.whatsapp_template_name},
                is_simulated=True,
                commit=False,
            )
            self.session.flush()
            simulated_msg_schema = SimulatorMessageSchema.model_validate(sim_record)

        msg_repo.update_delivery_state(
            locked_campaign,
            delivery_status=send_result.delivery_status,
            provider=send_result.provider,
            provider_message_id=send_result.provider_message_id,
            request_payload=send_result.request_payload,
            response_payload=send_result.response_payload,
            provider_error_code=send_result.error_code,
            provider_error_message=send_result.error_message,
            error_detail=send_result.error_message,
            provider_accepted_at=send_result.accepted_at,
            commit=True,
        )

        final_status = str(locked_campaign.delivery_status)
        if final_status == MessageDeliveryStatus.SENT_SIMULATED.value:
            action = "DISPATCHED_SIMULATED"
            detail = (
                f"Simulated WhatsApp message {send_result.provider_message_id} generated "
                f"for campaign {locked_campaign.id}."
            )
        elif final_status == MessageDeliveryStatus.PROVIDER_ACCEPTED.value:
            action = "DISPATCHED_PROVIDER_ACCEPTED"
            detail = (
                f"Meta WhatsApp Cloud API accepted message {send_result.provider_message_id} "
                f"for campaign {locked_campaign.id} (awaiting delivery webhook)."
            )
        else:
            action = "DISPATCH_FAILED"
            detail = (
                f"Dispatch ended with status {final_status}: "
                f"[{send_result.error_code}] {send_result.error_message}"
            )

        return CampaignDispatchResponse(
            campaign_id=locked_campaign.id,
            order_id=locked_campaign.order_id,
            external_order_id=external_order_id,
            provider=send_result.provider.value,
            previous_status=previous_status,
            delivery_status=final_status,
            dispatch_action=action,
            provider_message_id=locked_campaign.provider_message_id,
            provider_error_code=locked_campaign.provider_error_code,
            provider_error_message=locked_campaign.provider_error_message,
            is_simulated=send_result.is_simulated,
            retry_count=locked_campaign.retry_count,
            simulated_message=simulated_msg_schema,
            detail=detail,
        )

