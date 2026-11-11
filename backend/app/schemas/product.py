"""Pydantic v2 schemas for products and product recommendation mappings."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import CatalogueMetadataSource

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ProductBase(BaseModel):
    """Shared product fields with validation constraints."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="Unique SKU / finished-goods code (e.g., SAMPLE-001).",
    )
    product_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name of the product.",
    )
    is_rx: bool | None = Field(
        default=None,
        description="Explicit Rx/prescription status; None represents unknown metadata.",
    )
    is_active: bool | None = Field(
        default=None,
        description="Explicit catalogue active status; None represents unknown metadata.",
    )
    available_quantity: int | None = Field(
        default=None,
        ge=0,
        description="Available inventory count (>= 0); None represents unknown metadata.",
    )
    unit_price: Decimal | None = Field(
        default=None,
        ge=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        description="Optional unit price.",
    )
    currency: str = Field(
        default="INR",
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="3-letter ISO currency code.",
    )
    image_url: str | None = Field(default=None, max_length=512)
    product_url: str | None = Field(default=None, max_length=512)
    metadata_source: CatalogueMetadataSource = Field(
        default=CatalogueMetadataSource.WORKBOOK_ONLY,
        description="Provenance of the product's catalogue metadata.",
    )
    is_synthetic_metadata: bool = Field(
        default=False,
        description="True when catalogue metadata comes from a synthetic demonstration fixture.",
    )


class ProductCreate(ProductBase):
    """Schema for creating or upserting a product record."""


class ProductUpdate(BaseModel):
    """Schema for updating mutable product catalogue attributes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_rx: bool | None = None
    is_active: bool | None = None
    available_quantity: int | None = Field(default=None, ge=0)
    unit_price: Decimal | None = Field(default=None, ge=Decimal("0.00"), max_digits=10, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    image_url: str | None = Field(default=None, max_length=512)
    product_url: str | None = Field(default=None, max_length=512)
    metadata_source: CatalogueMetadataSource | None = None
    is_synthetic_metadata: bool | None = None


class ProductMappingBase(BaseModel):
    """Schema for a ranked source-to-target product recommendation mapping."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_product_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
    )
    recommended_product_code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_\-]+$",
    )
    recommendation_rank: int = Field(
        ...,
        ge=1,
        le=6,
        description="Ordinal co-purchase relevance rank (1..6).",
    )
    source_excel_row: int | None = Field(default=None, ge=2)

    @model_validator(mode="after")
    def validate_not_self_mapped(self) -> ProductMappingBase:
        if self.source_product_code == self.recommended_product_code:
            raise ValueError("source_product_code cannot recommend itself")
        return self


class ProductMappingCreate(ProductMappingBase):
    """Schema for creating a product mapping record."""


class ProductMappingRead(ProductMappingBase):
    """Schema for reading a persisted product mapping record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class ProductRead(ProductBase):
    """Schema for reading a persisted product record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime

    @field_validator("currency", mode="before")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value
