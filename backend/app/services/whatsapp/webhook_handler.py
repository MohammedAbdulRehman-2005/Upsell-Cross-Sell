"""Meta WhatsApp Cloud API webhook verification and status progression service."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.customer import Customer
from app.models.enums import (
    MessageDeliveryStatus,
    MessagingProvider,
    WebhookEventType,
    WebhookProcessingStatus,
)
from app.models.webhook import WebhookEvent
from app.models.whatsapp_message import WhatsAppMessage
from app.repositories.message_repository import MessageLogRepository
from app.schemas.whatsapp import MetaStatusWebhookResponseSchema
from app.services.whatsapp.models import OutboundTextMessageRequest, ParsedWebhookStatusUpdate
from app.services.whatsapp.providers import get_whatsapp_provider, parse_meta_status_entries

logger = logging.getLogger(__name__)

# Monotonic progression rank for Meta delivery states
STATUS_PROGRESSION_ORDER: dict[str, int] = {
    MessageDeliveryStatus.READY_FOR_DISPATCH.value: 0,
    MessageDeliveryStatus.DISPATCHING.value: 10,
    MessageDeliveryStatus.DELIVERY_UNKNOWN.value: 15,
    MessageDeliveryStatus.PROVIDER_ACCEPTED.value: 20,
    MessageDeliveryStatus.DELIVERY_PENDING.value: 30,
    MessageDeliveryStatus.DELIVERED.value: 40,
    MessageDeliveryStatus.READ.value: 50,
}


def verify_meta_webhook_challenge(
    *,
    hub_mode: str | None,
    hub_verify_token: str | None,
    hub_challenge: str | None,
    settings: Settings | None = None,
) -> str:
    """
    Validate Meta WhatsApp webhook subscription handshake (GET /api/v1/webhooks/whatsapp).

    Raises PermissionError if mode or verify_token is invalid, or ValueError if challenge is missing.
    """
    cfg = settings or get_settings()
    expected_token = cfg.whatsapp_webhook_verify_token.get_secret_value().strip()
    if not expected_token:
        raise PermissionError("WHATSAPP_WEBHOOK_VERIFY_TOKEN is not configured.")

    if hub_mode != "subscribe" or not hub_verify_token:
        raise PermissionError("Invalid hub.mode or missing hub.verify_token.")

    if not hmac.compare_digest(hub_verify_token.encode("utf-8"), expected_token.encode("utf-8")):
        raise PermissionError("Webhook verify token mismatch.")

    if hub_challenge is None or hub_challenge == "":
        raise ValueError("Missing hub.challenge parameter.")

    return str(hub_challenge)


def verify_meta_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    settings: Settings | None = None,
) -> bool:
    """
    Validate Meta `X-Hub-Signature-256` (`sha256=<hex>`) using `WHATSAPP_APP_SECRET`.

    Security rules:
    - If `WHATSAPP_APP_SECRET` is configured, a valid `X-Hub-Signature-256` header is mandatory.
    - If `WHATSAPP_APP_SECRET` is not configured:
      - In `production` or `staging`, fail closed (return False).
      - In `development` or `test`, allow unsigned requests only when no signature header is sent,
        or reject if a malformed signature header is sent.
    """
    cfg = settings or get_settings()
    app_secret = cfg.whatsapp_app_secret.get_secret_value().strip()

    if not app_secret:
        if cfg.environment in ("production", "staging"):
            return False
        if signature_header:
            return False
        return True

    if not signature_header:
        return False

    header_val = signature_header.strip()
    if header_val.startswith("sha256="):
        provided_hex = header_val[len("sha256="):].strip()
    else:
        return False

    expected_hex = hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(provided_hex.lower(), expected_hex.lower())


def parse_meta_inbound_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract inbound customer messages from a Meta WhatsApp webhook payload."""
    messages: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return messages
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return messages
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            raw_msgs = value.get("messages")
            if isinstance(raw_msgs, list):
                for m in raw_msgs:
                    if isinstance(m, dict):
                        messages.append(m)
    return messages


class MetaWebhookService:
    """Processes authenticated Meta WhatsApp status webhooks idempotently."""

    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def _claim_status_event(self, update: ParsedWebhookStatusUpdate) -> int | None:
        """Atomically insert a status event into `webhook_events` (unique event_id) for deduplication.

        Returns the new row id, or None when the same status event was already recorded.
        """
        event_id = update.event_key
        if len(event_id) > 128:
            event_id = "wa_status:" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()
        try:
            with self.session.begin_nested():
                record = WebhookEvent(
                    event_id=event_id,
                    event_type=WebhookEventType.WHATSAPP_STATUS_UPDATE,
                    external_order_id=None,
                    source="meta_whatsapp_cloud_api",
                    occurred_at=update.status_timestamp,
                    processing_status=WebhookProcessingStatus.PROCESSED,
                    payload={
                        "provider_message_id": update.provider_message_id,
                        "raw_status": update.raw_status,
                        "error_code": update.error_code,
                        "timestamp": update.status_timestamp.isoformat(),
                    },
                    processed_at=datetime.now(timezone.utc),
                )
                self.session.add(record)
                self.session.flush()
                return record.id
        except IntegrityError:
            return None

    @staticmethod
    def _is_valid_transition(
        current_status: str,
        target_status: MessageDeliveryStatus,
        campaign_provider: str | None,
    ) -> tuple[bool, str]:
        """
        Enforce strict state-machine rules for webhook status updates:
        - Simulated (`SENT_SIMULATED`) or `SUPPRESSED` campaigns can never be updated by Meta webhooks.
        - `DELIVERED` can never regress to `PROVIDER_ACCEPTED` or `DELIVERY_PENDING`.
        - `READ` can never regress to `DELIVERED`, `DELIVERY_PENDING`, or `PROVIDER_ACCEPTED`.
        - Once `DELIVERED` or `READ`, late failure webhooks cannot overwrite confirmed delivery.
        """
        if (
            current_status == MessageDeliveryStatus.SENT_SIMULATED.value
            or campaign_provider == MessagingProvider.WHATSAPP_SIMULATOR.value
        ):
            return False, "Simulated campaigns cannot be transitioned by external Meta webhooks."

        if current_status == MessageDeliveryStatus.SUPPRESSED.value:
            return False, "Suppressed campaigns cannot be transitioned by webhooks."

        target_str = target_status.value
        if current_status == target_str:
            return False, f"Campaign is already in status {current_status}."

        # Handle failure status updates from webhook
        if target_status in (
            MessageDeliveryStatus.FAILED_PERMANENT,
            MessageDeliveryStatus.FAILED_RETRYABLE,
        ):
            if current_status in (
                MessageDeliveryStatus.DELIVERED.value,
                MessageDeliveryStatus.READ.value,
            ):
                return (
                    False,
                    f"Cannot regress confirmed status {current_status} to {target_str}.",
                )
            return True, "Valid failure transition."

        # Handle forward progression (PROVIDER_ACCEPTED -> DELIVERY_PENDING -> DELIVERED -> READ)
        current_rank = STATUS_PROGRESSION_ORDER.get(current_status)
        target_rank = STATUS_PROGRESSION_ORDER.get(target_str)

        if current_rank is None or target_rank is None:
            return False, f"Transition from {current_status} to {target_str} is not permitted."

        if target_rank <= current_rank:
            return (
                False,
                f"Monotonic progression prevented regression from {current_status} to {target_str}.",
            )

        return True, "Valid forward progression."

    def process_inbound_messages(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Process inbound customer messages (STOP opt-out, START opt-in, HELP, text) idempotently."""
        raw_msgs = parse_meta_inbound_messages(payload)
        if not raw_msgs:
            return []

        results: list[dict[str, Any]] = []
        for msg in raw_msgs:
            wamid = str(msg.get("id") or "").strip()
            if not wamid:
                continue

            from_raw = str(msg.get("from") or "").strip()
            phone = from_raw if from_raw.startswith("+") else f"+{from_raw}"
            msg_type = str(msg.get("type") or "text")

            # Extract body text safely
            body = ""
            if msg_type == "text" and isinstance(msg.get("text"), dict):
                body = str(msg.get("text", {}).get("body") or "")
            elif msg_type == "button" and isinstance(msg.get("button"), dict):
                body = str(msg.get("button", {}).get("text") or "")
            elif msg_type == "interactive" and isinstance(msg.get("interactive"), dict):
                inter = msg["interactive"]
                btn = inter.get("button_reply") if isinstance(inter.get("button_reply"), dict) else {}
                lst = inter.get("list_reply") if isinstance(inter.get("list_reply"), dict) else {}
                body = str(btn.get("title") or lst.get("title") or "")
            else:
                body = str(msg.get("text", {}).get("body") or msg_type)

            # Deduplication claim in webhook_events
            event_key = f"wa_inbound:{wamid}"
            if len(event_key) > 128:
                event_key = "wa_inbound:" + hashlib.sha256(event_key.encode("utf-8")).hexdigest()

            try:
                with self.session.begin_nested():
                    ev = WebhookEvent(
                        event_id=event_key,
                        event_type=WebhookEventType.WHATSAPP_INBOUND_MESSAGE,
                        external_order_id=None,
                        source="meta_whatsapp_cloud_api",
                        occurred_at=datetime.now(timezone.utc),
                        processing_status=WebhookProcessingStatus.PROCESSED,
                        payload=msg,
                        processed_at=datetime.now(timezone.utc),
                    )
                    self.session.add(ev)
                    self.session.flush()
            except IntegrityError:
                results.append({"wamid": wamid, "phone": phone, "outcome": "DUPLICATE_INBOUND_IGNORED"})
                continue

            # Resolve customer
            customer = self.session.query(Customer).filter(Customer.phone_number == phone).first()
            now_utc = datetime.now(timezone.utc)

            # Persist inbound WhatsAppMessage
            wa_inbound = WhatsAppMessage(
                provider_message_id=wamid,
                provider="META_CLOUD_API",
                direction="INBOUND",
                customer_id=customer.id if customer else None,
                phone_number=phone,
                message_type=msg_type,
                body=body,
                raw_payload=msg,
                is_simulated=False,
            )
            self.session.add(wa_inbound)
            self.session.flush()

            # Deterministic keyword logic
            norm_body = body.strip().upper()
            action_taken = "RECEIVED"
            reply_text: str | None = None

            if norm_body in ("STOP", "UNSUBSCRIBE", "CANCEL", "OPT OUT", "OPTOUT"):
                action_taken = "CUSTOMER_OPTED_OUT"
                if customer:
                    customer.opted_out = True
                    customer.whatsapp_opt_in = False
                    customer.opt_out_recorded_at = now_utc
                reply_text = (
                    "You have been successfully unsubscribed from Plural WhatsApp updates. "
                    "Reply START to resubscribe anytime."
                )
            elif norm_body in ("START", "RESUBSCRIBE", "OPT IN", "OPTIN"):
                action_taken = "CUSTOMER_RESUBSCRIBED"
                if customer:
                    customer.opted_out = False
                    customer.whatsapp_opt_in = True
                    customer.consent_source = "WHATSAPP_INBOUND_START"
                    customer.consent_recorded_at = now_utc
                reply_text = (
                    "You have successfully subscribed to Plural WhatsApp updates. "
                    "Reply STOP anytime to unsubscribe."
                )
            elif norm_body in ("HELP", "SUPPORT", "INFO"):
                action_taken = "SUPPORT_INFO_SENT"
                reply_text = (
                    "Plural Support: For help with your order, contact support@pluralhealth.demo. "
                    "Reply STOP to unsubscribe from messages."
                )
            else:
                action_taken = "AUTO_ACK"
                reply_text = (
                    "Thank you for reaching out to Plural. "
                    "Reply STOP to opt out of updates or HELP for support."
                )

            # Record and dispatch outbound reply
            if reply_text:
                outbound_id = f"reply_{wamid}_{int(time.time())}"
                wa_outbound = WhatsAppMessage(
                    provider_message_id=outbound_id,
                    provider="META_CLOUD_API",
                    direction="OUTBOUND",
                    customer_id=customer.id if customer else None,
                    phone_number=phone,
                    message_type="text",
                    body=reply_text,
                    is_simulated=False,
                )
                self.session.add(wa_outbound)

                if (
                    self.settings.whatsapp_provider == "meta_cloud_api"
                    and self.settings.whatsapp_access_token.get_secret_value()
                ):
                    try:
                        prov = get_whatsapp_provider(settings=self.settings)
                        prov.send_text_message(
                            OutboundTextMessageRequest(
                                recipient_phone=phone,
                                body_text=reply_text,
                            )
                        )
                    except Exception as exc:
                        logger.error("Error sending WhatsApp reply: %s", exc)

            results.append(
                {
                    "wamid": wamid,
                    "phone": phone,
                    "action_taken": action_taken,
                    "outcome": "PROCESSED",
                }
            )

        self.session.commit()
        return results

    def process_status_webhook(
        self,
        payload: dict[str, Any],
    ) -> MetaStatusWebhookResponseSchema:
        inbound_results = self.process_inbound_messages(payload)
        updates = parse_meta_status_entries(payload)
        if not updates:
            return MetaStatusWebhookResponseSchema(
                status="IGNORED_NO_STATUS_EVENTS",
                events_received=0,
                inbound_messages_processed=len(inbound_results),
                details=inbound_results,
            )

        msg_repo = MessageLogRepository(self.session)
        campaigns_updated = 0
        duplicates_ignored = 0
        unknown_messages_ignored = 0
        regressed_statuses_ignored = 0
        details: list[dict[str, Any]] = []

        for update in updates:
            claimed_id = self._claim_status_event(update)
            if claimed_id is None:
                duplicates_ignored += 1
                details.append(
                    {
                        "provider_message_id": update.provider_message_id,
                        "raw_status": update.raw_status,
                        "outcome": "DUPLICATE_EVENT_IGNORED",
                    }
                )
                continue

            campaign = msg_repo.get_by_provider_message_id_for_update(update.provider_message_id)
            if campaign is None:
                unknown_messages_ignored += 1
                details.append(
                    {
                        "provider_message_id": update.provider_message_id,
                        "raw_status": update.raw_status,
                        "outcome": "UNKNOWN_PROVIDER_MESSAGE_ID_IGNORED",
                    }
                )
                continue

            current_status = str(campaign.delivery_status)
            campaign_provider = str(campaign.provider) if campaign.provider else None
            allowed, reason = self._is_valid_transition(
                current_status,
                update.mapped_status,
                campaign_provider,
            )

            if not allowed:
                regressed_statuses_ignored += 1
                details.append(
                    {
                        "campaign_id": campaign.id,
                        "provider_message_id": update.provider_message_id,
                        "previous_status": current_status,
                        "attempted_status": update.mapped_status.value,
                        "outcome": "TRANSITION_IGNORED",
                        "reason": reason,
                    }
                )
                continue

            delivered_at = campaign.delivered_at
            read_at = campaign.read_at
            accepted_at = campaign.provider_accepted_at

            if update.mapped_status == MessageDeliveryStatus.PROVIDER_ACCEPTED and accepted_at is None:
                accepted_at = update.status_timestamp
            elif update.mapped_status == MessageDeliveryStatus.DELIVERED:
                delivered_at = update.status_timestamp
            elif update.mapped_status == MessageDeliveryStatus.READ:
                read_at = update.status_timestamp
                if delivered_at is None:
                    delivered_at = update.status_timestamp

            merged_response = dict(campaign.response_payload or {})
            webhook_history = list(merged_response.get("webhook_status_history") or [])
            webhook_history.append(
                {
                    "raw_status": update.raw_status,
                    "mapped_status": update.mapped_status.value,
                    "timestamp": update.status_timestamp.isoformat(),
                    "error_code": update.error_code,
                    "error_message": update.error_message,
                }
            )
            merged_response["webhook_status_history"] = webhook_history

            msg_repo.update_delivery_state(
                campaign,
                delivery_status=update.mapped_status,
                response_payload=merged_response,
                provider_error_code=update.error_code,
                provider_error_message=update.error_message,
                error_detail=update.error_message,
                provider_accepted_at=accepted_at,
                delivered_at=delivered_at,
                read_at=read_at,
                commit=False,
            )
            campaigns_updated += 1
            details.append(
                {
                    "campaign_id": campaign.id,
                    "provider_message_id": update.provider_message_id,
                    "previous_status": current_status,
                    "new_status": update.mapped_status.value,
                    "outcome": "UPDATED",
                }
            )

        self.session.commit()
        return MetaStatusWebhookResponseSchema(
            status="PROCESSED",
            events_received=len(updates),
            campaigns_updated=campaigns_updated,
            duplicates_ignored=duplicates_ignored,
            unknown_messages_ignored=unknown_messages_ignored,
            regressed_statuses_ignored=regressed_statuses_ignored,
            inbound_messages_processed=len(inbound_results),
            details=details + inbound_results,
        )

