"""Stage 8: Pydantic schemas for the customer storefront and order tracking."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import OrderStatus

STRICT_E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")


class StorefrontCartItem(BaseModel):
    """Line item in customer shopping cart."""

    model_config = ConfigDict(extra="forbid")

    product_code: str = Field(min_length=1, max_length=64)
    quantity: int = Field(default=1, gt=0, le=100)


class StorefrontCheckoutRequest(BaseModel):
    """Customer checkout payload from the storefront."""

    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(min_length=2, max_length=128)
    customer_phone: str = Field(
        description="Customer phone in strict E.164 format with mandatory + prefix.",
    )
    delivery_address: str = Field(min_length=5, max_length=512)
    items: list[StorefrontCartItem] = Field(min_length=1)
    whatsapp_opt_in: bool = Field(
        default=False,
        description="Explicit WhatsApp marketing consent. Unchecked by default.",
    )

    @field_validator("customer_phone")
    @classmethod
    def validate_strict_e164(cls, val: str) -> str:
        cleaned = val.strip() if isinstance(val, str) else ""
        if not STRICT_E164_REGEX.match(cleaned):
            raise ValueError(
                f"Customer phone '{val}' must be in strict E.164 format (e.g. +919876543210) "
                "with a leading '+' and 8 to 15 digits without spaces or dashes."
            )
        return cleaned

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, val: str) -> str:
        cleaned = val.strip()
        if not cleaned:
            raise ValueError("Customer name cannot be blank or whitespace.")
        return cleaned

    @field_validator("delivery_address")
    @classmethod
    def validate_address(cls, val: str) -> str:
        cleaned = val.strip()
        if not cleaned:
            raise ValueError("Delivery address cannot be blank or whitespace.")
        return cleaned


class StorefrontOrderResponse(BaseModel):
    """Response after placing an order on the storefront."""

    model_config = ConfigDict(from_attributes=True)

    order_id: int
    external_order_id: str
    customer_id: int
    customer_name: str
    customer_phone: str
    order_status: OrderStatus | str
    total_amount: float
    item_count: int
    whatsapp_opt_in: bool
    is_demo_order: bool
    tracking_url: str
    estimated_delivery_seconds: float
    created_at: datetime


class StorefrontTrackingStep(BaseModel):
    """Individual milestone in the delivery tracking stepper."""

    status: str
    label: str
    timestamp: str | None = None
    is_completed: bool = False
    is_current: bool = False


class StorefrontTrackingResponse(BaseModel):
    """Real-time tracking status and milestone details for a demo storefront order."""

    model_config = ConfigDict(extra="ignore")

    order_id: int
    external_order_id: str
    order_status: str
    customer_name: str
    delivery_address: str | None = None
    is_demo_order: bool = True
    steps: list[StorefrontTrackingStep]
    seconds_remaining: float | None = None
    delivery_completed: bool = False
    demo_banner: str
    whatsapp_opt_in: bool = False
    whatsapp_campaign_status: str | None = None
    whatsapp_campaign_id: int | None = None
    whatsapp_message_status: str | None = None
    recommended_skus: list[str] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
