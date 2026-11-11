"""Internal data models for WhatsApp providers, dispatch coordinator, and status webhooks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.enums import MessageDeliveryStatus, MessagingProvider


@dataclass(frozen=True)
class RecommendationCardItem:
    """Product recommendation item formatted for outbound WhatsApp template or simulator card."""

    slot_number: int
    product_code: str
    product_name: str
    category: str | None = None
    unit_price: float | None = None
    source_product_code: str | None = None
    source_rank: int | None = None
    metadata_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_number": self.slot_number,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "category": self.category,
            "unit_price": self.unit_price,
            "source_product_code": self.source_product_code,
            "source_rank": self.source_rank,
            "metadata_source": self.metadata_source,
        }


@dataclass(frozen=True)
class OutboundTemplateMessageRequest:
    """Normalized request passed to a WhatsApp provider for template message dispatch."""

    campaign_id: int
    idempotency_key: str
    order_id: int
    external_order_id: str
    order_number: str | None
    customer_id: int
    external_customer_id: str
    recipient_phone: str
    template_name: str
    template_language: str
    recommendations: list[RecommendationCardItem] = field(default_factory=list)


@dataclass(frozen=True)
class OutboundTextMessageRequest:
    """Normalized request passed to a WhatsApp provider for follow-up text message dispatch."""

    campaign_id: int
    recipient_phone: str
    body_text: str


@dataclass(frozen=True)
class ProviderConfigurationStatus:
    """Result of validating a WhatsApp provider's configuration."""

    provider: MessagingProvider
    is_valid: bool
    missing_settings: list[str] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True)
class ProviderSendResult:
    """Result returned by a WhatsApp provider after attempting message dispatch."""

    provider: MessagingProvider
    delivery_status: MessageDeliveryStatus
    provider_message_id: str | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    response_payload: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    is_simulated: bool = False
    rendered_body_text: str | None = None
    accepted_at: datetime | None = None


@dataclass(frozen=True)
class ParsedWebhookStatusUpdate:
    """Normalized status update extracted from a Meta WhatsApp webhook payload."""

    event_key: str
    provider_message_id: str
    raw_status: str
    mapped_status: MessageDeliveryStatus
    recipient_id: str | None
    status_timestamp: datetime
    error_code: str | None = None
    error_message: str | None = None
    raw_entry: dict[str, Any] = field(default_factory=dict)
