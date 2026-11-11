"""Pydantic v2 schemas for orders, order line items, and order delivery events."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import OrderStatus, WebhookEventType


class OrderLineItemBase(BaseModel):
    """Validated order line item."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line_index: int = Field(
        ...,
        ge=0,
        description="Stable 0-based order line position.",
    )
    product_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="Ordered SKU code.",
    )
    product_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    quantity: int = Field(
        default=1,
        ge=1,
        description="Ordered quantity (must be >= 1).",
    )


class OrderLineItemCreate(OrderLineItemBase):
    """Schema for creating an order line item."""


class OrderLineItemRead(OrderLineItemBase):
    """Schema for reading a persisted order line item."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    created_at: datetime
    updated_at: datetime


class OrderCreate(BaseModel):
    """Validated order creation payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_order_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="Unique external order identifier.",
    )
    customer_id: int = Field(..., ge=1, description="Internal customer ID.")
    order_status: OrderStatus = Field(
        default=OrderStatus.PENDING,
        description="Current lifecycle status of the order.",
    )
    delivered_at: datetime | None = Field(default=None)
    line_items: list[OrderLineItemCreate] = Field(
        ...,
        min_length=1,
        description="At least one order line item is required.",
    )

    @model_validator(mode="after")
    def validate_unique_line_indices(self) -> OrderCreate:
        seen_indices: set[int] = set()
        for item in self.line_items:
            if item.line_index in seen_indices:
                raise ValueError(f"Duplicate line_index {item.line_index} in order line_items")
            seen_indices.add(item.line_index)
        return self


class OrderRead(BaseModel):
    """Schema for reading a persisted order and its line items."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    external_order_id: str
    customer_id: int
    order_status: OrderStatus
    delivered_at: datetime | None
    line_items: list[OrderLineItemRead]
    created_at: datetime
    updated_at: datetime


class OrderDeliveryEventLineItem(BaseModel):
    """Line item embedded in an inbound order delivery event payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional explicit 0-based line index; defaults to array position if omitted.",
    )
    product_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
    )
    product_name: str | None = Field(default=None, min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1)


class OrderDeliveryEvent(BaseModel):
    """Inbound order delivery event schema (e.g. webhook payload)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_\-:.]+$",
        description="Unique idempotency identifier for the webhook event.",
    )
    event_type: WebhookEventType = Field(
        default=WebhookEventType.ORDER_DELIVERED,
        description="Type of order lifecycle event.",
    )
    order_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="External order ID.",
    )
    occurred_at: datetime | None = Field(
        default=None,
        description="Timestamp when the delivery event occurred in the upstream source.",
    )
    source: str = Field(
        default="order_service",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-.]+$",
        description="Upstream system that emitted the delivery event.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Optional event metadata dictionary.",
    )
    customer_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="Optional external customer ID reported in the event.",
    )
    customer_phone: str | None = Field(
        default=None,
        min_length=9,
        max_length=16,
        pattern=r"^\+[1-9]\d{7,14}$",
    )
    order_status: OrderStatus | None = Field(
        default=None,
        description="Optional order status reported in the event (persisted database status is authoritative).",
    )
    delivered_at: datetime | None = Field(default=None)
    line_items: list[OrderDeliveryEventLineItem] | None = Field(
        default=None,
        min_length=1,
        description="Optional list of ordered line items (persisted database line items are authoritative).",
    )

    @model_validator(mode="after")
    def normalize_and_validate_line_indices(self) -> OrderDeliveryEvent:
        if self.line_items is None:
            return self
        seen_indices: set[int] = set()
        for idx, item in enumerate(self.line_items):
            resolved_index = item.line_index if item.line_index is not None else idx
            if resolved_index in seen_indices:
                raise ValueError(f"Duplicate line_index {resolved_index} in delivery event line_items")
            item.line_index = resolved_index
            seen_indices.add(resolved_index)
        return self
