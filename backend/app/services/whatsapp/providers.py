"""WhatsApp provider abstraction and concrete Simulator & Meta Cloud API adapters."""
from __future__ import annotations

import abc
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.models.enums import MessageDeliveryStatus, MessagingProvider
from app.services.whatsapp.models import (
    OutboundTemplateMessageRequest,
    OutboundTextMessageRequest,
    ParsedWebhookStatusUpdate,
    ProviderConfigurationStatus,
    ProviderSendResult,
)

logger = logging.getLogger(__name__)

STRICT_E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")


def _redact_secrets(text: str | None, secrets: list[str]) -> str:
    """Ensure access tokens and app secrets are never exposed in logs or error payloads."""
    if not text:
        return ""
    sanitized = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", sanitized)
    return sanitized


def parse_meta_status_entries(payload: dict[str, Any]) -> list[ParsedWebhookStatusUpdate]:
    """
    Extract provider status updates from a Meta WhatsApp webhook payload.

    Important security invariant:
    - Only inspects `entry[].changes[].value.statuses[]` (authenticated Meta status events).
    - Never trusts `entry[].changes[].value.messages[]` (customer-supplied inbound messages)
      as delivery status updates.
    """
    updates: list[ParsedWebhookStatusUpdate] = []
    if not isinstance(payload, dict):
        return updates

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return updates

    status_map: dict[str, MessageDeliveryStatus] = {
        "accepted": MessageDeliveryStatus.PROVIDER_ACCEPTED,
        "sent": MessageDeliveryStatus.DELIVERY_PENDING,
        "delivered": MessageDeliveryStatus.DELIVERED,
        "read": MessageDeliveryStatus.READ,
        "failed": MessageDeliveryStatus.FAILED_PERMANENT,
    }

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
            statuses = value.get("statuses")
            if not isinstance(statuses, list):
                continue

            for status_obj in statuses:
                if not isinstance(status_obj, dict):
                    continue
                wamid = str(status_obj.get("id") or "").strip()
                raw_status = str(status_obj.get("status") or "").strip().lower()
                if not wamid or raw_status not in status_map:
                    continue

                mapped_status = status_map[raw_status]
                raw_ts = status_obj.get("timestamp")
                status_timestamp = datetime.now(timezone.utc)
                if raw_ts is not None:
                    try:
                        status_timestamp = datetime.fromtimestamp(int(str(raw_ts)), tz=timezone.utc)
                    except (ValueError, TypeError, OSError):
                        status_timestamp = datetime.now(timezone.utc)

                error_code: str | None = None
                error_message: str | None = None
                errors = status_obj.get("errors")
                if isinstance(errors, list) and errors:
                    first_err = errors[0] if isinstance(errors[0], dict) else {}
                    if first_err.get("code") is not None:
                        error_code = f"META_{first_err.get('code')}"
                    title = first_err.get("title") or first_err.get("message") or "Webhook reported failure"
                    details = first_err.get("error_data", {}).get("details") if isinstance(first_err.get("error_data"), dict) else None
                    error_message = f"{title}: {details}" if details else str(title)

                # Check if webhook error is retryable (e.g. Meta 130429 rate limit or 131053 transient)
                if mapped_status == MessageDeliveryStatus.FAILED_PERMANENT and error_code in {
                    "META_130429",
                    "META_80007",
                    "META_131053",
                    "META_131000",
                }:
                    mapped_status = MessageDeliveryStatus.FAILED_RETRYABLE

                raw_hash = hashlib.sha256(
                    json.dumps(
                        {
                            "id": wamid,
                            "status": raw_status,
                            "timestamp": str(raw_ts or ""),
                            "error_code": error_code or "",
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()[:24]
                event_key = f"wa_status:{wamid}:{raw_status}:{raw_hash}"

                updates.append(
                    ParsedWebhookStatusUpdate(
                        event_key=event_key,
                        provider_message_id=wamid,
                        raw_status=raw_status,
                        mapped_status=mapped_status,
                        recipient_id=str(status_obj.get("recipient_id") or "") or None,
                        status_timestamp=status_timestamp,
                        error_code=error_code,
                        error_message=error_message,
                        raw_entry=status_obj,
                    )
                )

    return updates


class WhatsAppProvider(abc.ABC):
    """Common interface implemented by both Simulator and Meta WhatsApp Cloud API providers."""

    provider_type: MessagingProvider

    @abc.abstractmethod
    def validate_configuration(self) -> ProviderConfigurationStatus:
        """Verify that required credentials and settings for this provider are present."""

    @abc.abstractmethod
    def send_template_message(
        self,
        request: OutboundTemplateMessageRequest,
    ) -> ProviderSendResult:
        """Send an outbound post-delivery upsell template message."""

    @abc.abstractmethod
    def send_text_message(
        self,
        request: OutboundTextMessageRequest,
    ) -> ProviderSendResult:
        """Send an outbound follow-up text message."""

    def parse_status_webhook(
        self,
        payload: dict[str, Any],
    ) -> list[ParsedWebhookStatusUpdate]:
        """Parse status updates from a webhook payload."""
        return parse_meta_status_entries(payload)


class SimulatorWhatsAppProvider(WhatsAppProvider):
    """
    Local interactive customer simulator provider.

    - Requires no external credentials.
    - Explicitly marks all messages as simulated (`is_simulated=True`).
    - Sets campaign status to `SENT_SIMULATED` (never claims real `DELIVERED`).
    """

    provider_type = MessagingProvider.WHATSAPP_SIMULATOR

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate_configuration(self) -> ProviderConfigurationStatus:
        return ProviderConfigurationStatus(
            provider=MessagingProvider.WHATSAPP_SIMULATOR,
            is_valid=True,
            missing_settings=[],
            message="Simulator provider is active and ready (no external credentials required).",
        )

    @staticmethod
    def render_simulated_template_body(request: OutboundTemplateMessageRequest) -> str:
        order_ref = request.order_number or request.external_order_id
        lines = [
            "[SIMULATED WHATSAPP MESSAGE — DEMO / SIMULATOR MODE]",
            f"Hello! Your Plural order {order_ref} has been delivered.",
            "Based on the items in your order, here are your personalized OTC wellness & care recommendations:",
            "",
        ]
        for item in request.recommendations:
            price_str = f" — ₹{item.unit_price:.2f}" if item.unit_price is not None else ""
            cat_str = f" [{item.category}]" if item.category else ""
            lines.append(
                f"{item.slot_number}. {item.product_name} ({item.product_code}){cat_str}{price_str}"
            )
        lines.extend(
            [
                "",
                "Tap any recommendation card below to view details, express interest, or browse all options.",
            ]
        )
        return "\n".join(lines)

    def send_template_message(
        self,
        request: OutboundTemplateMessageRequest,
    ) -> ProviderSendResult:
        if not STRICT_E164_REGEX.match(request.recipient_phone or ""):
            return ProviderSendResult(
                provider=MessagingProvider.WHATSAPP_SIMULATOR,
                delivery_status=MessageDeliveryStatus.FAILED_PERMANENT,
                error_code="INVALID_RECIPIENT_PHONE",
                error_message="Recipient phone number must be in strict E.164 format.",
                is_simulated=True,
            )

        now = datetime.now(timezone.utc)
        sim_message_id = f"sim_wamid.{request.campaign_id}.{uuid.uuid4().hex[:12]}"
        rendered_body = self.render_simulated_template_body(request)

        request_payload = {
            "provider": MessagingProvider.WHATSAPP_SIMULATOR.value,
            "is_simulated": True,
            "to": request.recipient_phone,
            "type": "template",
            "template": {
                "name": request.template_name,
                "language": {"code": request.template_language},
            },
            "recommendations": [item.to_dict() for item in request.recommendations],
        }
        response_payload = {
            "provider": MessagingProvider.WHATSAPP_SIMULATOR.value,
            "is_simulated": True,
            "simulated_message_id": sim_message_id,
            "status": MessageDeliveryStatus.SENT_SIMULATED.value,
            "timestamp": now.isoformat(),
        }

        return ProviderSendResult(
            provider=MessagingProvider.WHATSAPP_SIMULATOR,
            delivery_status=MessageDeliveryStatus.SENT_SIMULATED,
            provider_message_id=sim_message_id,
            request_payload=request_payload,
            response_payload=response_payload,
            is_simulated=True,
            rendered_body_text=rendered_body,
            accepted_at=now,
        )

    def send_text_message(
        self,
        request: OutboundTextMessageRequest,
    ) -> ProviderSendResult:
        now = datetime.now(timezone.utc)
        sim_message_id = f"sim_wamid.{request.campaign_id}.{uuid.uuid4().hex[:12]}"
        return ProviderSendResult(
            provider=MessagingProvider.WHATSAPP_SIMULATOR,
            delivery_status=MessageDeliveryStatus.SENT_SIMULATED,
            provider_message_id=sim_message_id,
            request_payload={
                "provider": MessagingProvider.WHATSAPP_SIMULATOR.value,
                "is_simulated": True,
                "to": request.recipient_phone,
                "type": "text",
                "text": {"body": request.body_text},
            },
            response_payload={
                "provider": MessagingProvider.WHATSAPP_SIMULATOR.value,
                "is_simulated": True,
                "simulated_message_id": sim_message_id,
                "status": MessageDeliveryStatus.SENT_SIMULATED.value,
                "timestamp": now.isoformat(),
            },
            is_simulated=True,
            rendered_body_text=request.body_text,
            accepted_at=now,
        )


class MetaCloudWhatsAppProvider(WhatsAppProvider):
    """
    Production HTTP adapter for the Meta WhatsApp Cloud API.

    - Uses `httpx` with explicit finite timeouts.
    - Never logs or persists access tokens or secrets.
    - Never falls back to the simulator on failure.
    - Never marks a campaign `DELIVERED` on HTTP 200 acceptance (`PROVIDER_ACCEPTED`).
    - Distinguishes retryable (`FAILED_RETRYABLE`), permanent (`FAILED_PERMANENT`),
      and ambiguous post-request timeout (`DELIVERY_UNKNOWN`) failures.
    """

    provider_type = MessagingProvider.META_CLOUD_API

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client

    def _secrets(self) -> list[str]:
        return [
            s
            for s in [
                self.settings.whatsapp_access_token.get_secret_value(),
                self.settings.whatsapp_app_secret.get_secret_value(),
                self.settings.whatsapp_webhook_verify_token.get_secret_value(),
            ]
            if s
        ]

    def validate_configuration(self) -> ProviderConfigurationStatus:
        missing: list[str] = []
        if not (self.settings.whatsapp_access_token.get_secret_value() or "").strip():
            missing.append("WHATSAPP_ACCESS_TOKEN")
        if not (self.settings.whatsapp_phone_number_id or "").strip():
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        if not (self.settings.whatsapp_template_name or "").strip():
            missing.append("WHATSAPP_TEMPLATE_NAME")
        if not (self.settings.whatsapp_template_language or "").strip():
            missing.append("WHATSAPP_TEMPLATE_LANGUAGE")

        if missing:
            return ProviderConfigurationStatus(
                provider=MessagingProvider.META_CLOUD_API,
                is_valid=False,
                missing_settings=missing,
                message=f"Meta WhatsApp Cloud API is not configured. Missing: {', '.join(missing)}",
            )
        return ProviderConfigurationStatus(
            provider=MessagingProvider.META_CLOUD_API,
            is_valid=True,
            missing_settings=[],
            message="Meta WhatsApp Cloud API credentials and template settings are configured.",
        )

    def _messages_endpoint(self) -> str:
        base = self.settings.whatsapp_base_url.rstrip("/")
        version = self.settings.whatsapp_api_version.strip("/")
        phone_id = (self.settings.whatsapp_phone_number_id or "").strip("/")
        return f"{base}/{version}/{phone_id}/messages"

    @staticmethod
    def build_meta_template_payload(request: OutboundTemplateMessageRequest) -> dict[str, Any]:
        """Build the official Meta WhatsApp Cloud API template request payload."""
        order_ref = request.order_number or request.external_order_id
        rec_summary = ", ".join(
            f"{item.product_name} ({item.product_code})"
            for item in request.recommendations
        )
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": request.recipient_phone,
            "type": "template",
            "template": {
                "name": request.template_name,
                "language": {
                    "code": request.template_language,
                },
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": order_ref},
                            {"type": "text", "text": rec_summary},
                        ],
                    }
                ],
            },
        }

    def _classify_http_failure(
        self,
        status_code: int,
        response_data: dict[str, Any],
    ) -> tuple[MessageDeliveryStatus, str, str]:
        """Map Meta Graph API HTTP status and error codes to deterministic delivery statuses."""
        err_obj = response_data.get("error") if isinstance(response_data.get("error"), dict) else {}
        meta_code = err_obj.get("code")
        meta_subcode = err_obj.get("error_subcode")
        raw_message = str(
            err_obj.get("message")
            or err_obj.get("error_user_msg")
            or f"Meta Graph API returned HTTP {status_code}"
        )
        safe_message = _redact_secrets(raw_message, self._secrets())

        # 1. Authentication / authorization failure (401, 403, or OAuth code 190)
        if status_code in (401, 403) or meta_code in (190, 10, 200):
            return (
                MessageDeliveryStatus.FAILED_PERMANENT,
                f"AUTH_ERROR_{meta_code or status_code}",
                safe_message,
            )

        # 2. Rate limit (HTTP 429 or Meta rate limit codes)
        if status_code == 429 or meta_code in (4, 80007, 130429, 131048, 131056):
            return (
                MessageDeliveryStatus.FAILED_RETRYABLE,
                f"RATE_LIMITED_{meta_code or status_code}",
                safe_message,
            )

        # 3. Invalid template / template parameter errors
        msg_lower = safe_message.lower()
        if meta_code in (132000, 132001, 132005, 132007, 132012, 132015, 132016) or "template" in msg_lower:
            return (
                MessageDeliveryStatus.FAILED_PERMANENT,
                f"INVALID_TEMPLATE_{meta_code or status_code}",
                safe_message,
            )

        # 4. Recipient phone number errors
        if (
            meta_code in (131026, 131030, 131031, 131047, 131051)
            or (status_code == 400 and ("recipient" in msg_lower or "phone" in msg_lower or meta_code == 100))
        ):
            return (
                MessageDeliveryStatus.FAILED_PERMANENT,
                f"RECIPIENT_ERROR_{meta_code or status_code}",
                safe_message,
            )

        # 5. Transient 5xx server errors or Meta temporary error 131000 / 131053 / 2
        if status_code >= 500 or meta_code in (1, 2, 131000, 131053):
            return (
                MessageDeliveryStatus.FAILED_RETRYABLE,
                f"TRANSIENT_PROVIDER_ERROR_{meta_code or status_code}",
                safe_message,
            )

        # 6. Any other 4xx client error is permanent
        code_suffix = f"{meta_code}_{meta_subcode}" if meta_subcode else str(meta_code or status_code)
        return (
            MessageDeliveryStatus.FAILED_PERMANENT,
            f"META_CLIENT_ERROR_{code_suffix}",
            safe_message,
        )

    def _execute_post(
        self,
        payload: dict[str, Any],
    ) -> ProviderSendResult:
        config_status = self.validate_configuration()
        if not config_status.is_valid:
            return ProviderSendResult(
                provider=MessagingProvider.META_CLOUD_API,
                delivery_status=MessageDeliveryStatus.FAILED_PERMANENT,
                request_payload=payload,
                response_payload={"error": config_status.message, "missing_settings": config_status.missing_settings},
                error_code="PROVIDER_NOT_CONFIGURED",
                error_message=config_status.message,
                is_simulated=False,
            )

        recipient = str(payload.get("to") or "")
        if not STRICT_E164_REGEX.match(recipient):
            return ProviderSendResult(
                provider=MessagingProvider.META_CLOUD_API,
                delivery_status=MessageDeliveryStatus.FAILED_PERMANENT,
                request_payload=payload,
                response_payload={"error": "Recipient phone failed strict E.164 check."},
                error_code="INVALID_RECIPIENT_PHONE",
                error_message="Recipient phone number must be in strict E.164 format (+[1-9]\\d{7,14}).",
                is_simulated=False,
            )

        url = self._messages_endpoint()
        headers = {
            "Authorization": f"Bearer {self.settings.whatsapp_access_token.get_secret_value()}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(self.settings.whatsapp_http_timeout_seconds)

        try:
            if self._http_client is not None:
                response = self._http_client.post(url, json=payload, headers=headers, timeout=timeout)
            else:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, json=payload, headers=headers)
        except httpx.ConnectTimeout as exc:
            # Connection was never established, so request was not transmitted
            safe_err = _redact_secrets(str(exc), self._secrets())
            return ProviderSendResult(
                provider=MessagingProvider.META_CLOUD_API,
                delivery_status=MessageDeliveryStatus.FAILED_RETRYABLE,
                request_payload=payload,
                response_payload={"exception": "ConnectTimeout", "detail": safe_err},
                error_code="CONNECT_TIMEOUT",
                error_message=f"Connection timeout before request dispatch: {safe_err}",
                is_simulated=False,
            )
        except httpx.TimeoutException as exc:
            # ReadTimeout / WriteTimeout / PoolTimeout after request may have left the client:
            # Ambiguous outcome -> DELIVERY_UNKNOWN to prevent blind duplicate sends.
            safe_err = _redact_secrets(str(exc), self._secrets())
            return ProviderSendResult(
                provider=MessagingProvider.META_CLOUD_API,
                delivery_status=MessageDeliveryStatus.DELIVERY_UNKNOWN,
                request_payload=payload,
                response_payload={"exception": type(exc).__name__, "detail": safe_err},
                error_code="AMBIGUOUS_NETWORK_TIMEOUT",
                error_message=(
                    "Network timeout occurred while awaiting Meta Graph API response; "
                    f"delivery state is ambiguous ({type(exc).__name__}): {safe_err}"
                ),
                is_simulated=False,
            )
        except httpx.ConnectError as exc:
            safe_err = _redact_secrets(str(exc), self._secrets())
            return ProviderSendResult(
                provider=MessagingProvider.META_CLOUD_API,
                delivery_status=MessageDeliveryStatus.FAILED_RETRYABLE,
                request_payload=payload,
                response_payload={"exception": "ConnectError", "detail": safe_err},
                error_code="NETWORK_CONNECT_ERROR",
                error_message=f"Unable to connect to Meta Graph API: {safe_err}",
                is_simulated=False,
            )
        except httpx.HTTPError as exc:
            safe_err = _redact_secrets(str(exc), self._secrets())
            return ProviderSendResult(
                provider=MessagingProvider.META_CLOUD_API,
                delivery_status=MessageDeliveryStatus.DELIVERY_UNKNOWN,
                request_payload=payload,
                response_payload={"exception": type(exc).__name__, "detail": safe_err},
                error_code="AMBIGUOUS_HTTP_ERROR",
                error_message=f"Unexpected HTTP transport error: {safe_err}",
                is_simulated=False,
            )

        try:
            response_data = response.json()
            if not isinstance(response_data, dict):
                response_data = {"raw": str(response_data)}
        except Exception:
            response_data = {"raw_text": _redact_secrets(response.text[:1000], self._secrets())}

        if response.status_code in (200, 201):
            messages_list = response_data.get("messages")
            provider_msg_id: str | None = None
            if isinstance(messages_list, list) and messages_list and isinstance(messages_list[0], dict):
                provider_msg_id = str(messages_list[0].get("id") or "").strip() or None

            if not provider_msg_id:
                return ProviderSendResult(
                    provider=MessagingProvider.META_CLOUD_API,
                    delivery_status=MessageDeliveryStatus.DELIVERY_UNKNOWN,
                    request_payload=payload,
                    response_payload=response_data,
                    error_code="MISSING_PROVIDER_MESSAGE_ID",
                    error_message="Meta Graph API returned HTTP 200 without a provider message ID.",
                    is_simulated=False,
                )

            return ProviderSendResult(
                provider=MessagingProvider.META_CLOUD_API,
                delivery_status=MessageDeliveryStatus.PROVIDER_ACCEPTED,
                provider_message_id=provider_msg_id,
                request_payload=payload,
                response_payload=response_data,
                is_simulated=False,
                accepted_at=datetime.now(timezone.utc),
            )

        mapped_status, error_code, error_message = self._classify_http_failure(
            response.status_code,
            response_data,
        )
        return ProviderSendResult(
            provider=MessagingProvider.META_CLOUD_API,
            delivery_status=mapped_status,
            request_payload=payload,
            response_payload=response_data,
            error_code=error_code,
            error_message=error_message,
            is_simulated=False,
        )

    def send_template_message(
        self,
        request: OutboundTemplateMessageRequest,
    ) -> ProviderSendResult:
        payload = self.build_meta_template_payload(request)
        return self._execute_post(payload)

    def send_text_message(
        self,
        request: OutboundTextMessageRequest,
    ) -> ProviderSendResult:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": request.recipient_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": request.body_text,
            },
        }
        return self._execute_post(payload)


def get_whatsapp_provider(
    settings: Settings | None = None,
    http_client: httpx.Client | None = None,
) -> WhatsAppProvider:
    """
    Resolve the configured WhatsApp provider.

    Invariant:
    - Never automatically switches from `meta_cloud_api` to `simulator` when Meta credentials
      are missing or when Meta fails. Simulator is used only when `WHATSAPP_PROVIDER="simulator"`.
    """
    resolved_settings = settings or get_settings()
    if resolved_settings.whatsapp_provider == "meta_cloud_api":
        return MetaCloudWhatsAppProvider(settings=resolved_settings, http_client=http_client)
    return SimulatorWhatsAppProvider(settings=resolved_settings)

