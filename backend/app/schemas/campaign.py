"""Pydantic v2 schemas for Stage 5 order-delivery event processing and campaign orchestration."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CampaignSuppressionReason,
    DeliveryEventOutcome,
    MessageDeliveryStatus,
)
from app.schemas.order import OrderDeliveryEventLineItem


class OrderDeliveredWebhookRequest(BaseModel):
    """Inbound payload for POST /api/v1/webhooks/order-delivered."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_\-:.]+$",
        description="Unique idempotency identifier for the delivery event.",
    )
    event_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Event type (must be 'order.delivered').",
    )
    order_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="External order ID of the delivered order.",
    )
    occurred_at: datetime = Field(
        ...,
        description="ISO-8601 timestamp when the delivery event occurred.",
    )
    source: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-.]+$",
        description="Source system emitting the event (e.g., 'order_service').",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Optional contextual metadata from the upstream event emitter.",
    )
    customer_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="Optional external customer ID assertion (checked against persisted order if provided).",
    )
    customer_phone: str | None = Field(
        default=None,
        description="Optional phone number assertion (persisted customer profile is authoritative).",
    )
    order_status: str | None = Field(
        default=None,
        description="Optional order status reported in event (persisted database status is authoritative).",
    )
    delivered_at: datetime | None = Field(
        default=None,
        description="Optional delivery timestamp.",
    )
    line_items: list[OrderDeliveryEventLineItem] | None = Field(
        default=None,
        min_length=1,
        description="Optional line items in event payload (persisted database line items are authoritative).",
    )


class CampaignRecordSchema(BaseModel):
    """Persisted campaign / message preparation record returned by Stage 5 orchestration."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    idempotency_key: str
    order_id: int
    customer_id: int
    campaign_type: str
    trigger_event_id: str | None = None
    delivery_status: MessageDeliveryStatus
    recommendation_status: str | None = None
    suppression_reason: CampaignSuppressionReason | str | None = None
    recommended_skus: list[str]
    decision_trace: dict[str, Any] | None = None
    metadata_provenance: dict[str, Any] | None = None
    error_detail: str | None = None
    created_at: datetime
    updated_at: datetime


class OrderDeliveryWebhookResponse(BaseModel):
    """Structured API response for POST /api/v1/webhooks/order-delivered."""

    event_id: str
    event_type: str
    order_id: str
    outcome: DeliveryEventOutcome
    idempotent_replay: bool = False
    campaign_id: int | None = None
    campaign_type: str = "POST_DELIVERY_UPSELL"
    campaign_status: MessageDeliveryStatus | None = None
    recommendation_status: str | None = None
    suppression_reason: CampaignSuppressionReason | str | None = None
    recommended_skus: list[str] = Field(default_factory=list)
    metadata_provenance: dict[str, Any] | None = None
    detail: str
