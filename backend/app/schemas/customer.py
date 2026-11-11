"""Pydantic v2 schemas for customers and messaging consent."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerBase(BaseModel):
    """Shared customer fields with phone and consent validation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_customer_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="External customer identifier.",
    )
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone_number: str = Field(
        ...,
        min_length=9,
        max_length=16,
        pattern=r"^\+[1-9]\d{7,14}$",
        description="Strict E.164 international phone number with leading '+' (8-15 digits).",
    )
    whatsapp_opt_in: bool = Field(
        default=True,
        description="Whether customer has consented to WhatsApp notifications.",
    )
    opted_out: bool = Field(
        default=False,
        description="Whether customer has explicitly opted out of messages.",
    )
    consent_source: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Source system or channel where messaging consent was captured.",
    )
    consent_recorded_at: datetime | None = Field(
        default=None,
        description="Timestamp when messaging consent was captured or updated.",
    )


class CustomerCreate(CustomerBase):
    """Schema for creating a customer."""


class CustomerUpdate(BaseModel):
    """Schema for updating customer profile or consent state."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone_number: str | None = Field(
        default=None,
        min_length=9,
        max_length=16,
        pattern=r"^\+[1-9]\d{7,14}$",
    )
    whatsapp_opt_in: bool | None = None
    opted_out: bool | None = None
    consent_source: str | None = Field(default=None, min_length=1, max_length=64)
    consent_recorded_at: datetime | None = None


class CustomerRead(BaseModel):
    """Schema for reading a persisted customer record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    external_customer_id: str
    full_name: str | None
    phone_number: str | None
    whatsapp_opt_in: bool | None
    opted_out: bool | None
    consent_source: str | None = None
    consent_recorded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
