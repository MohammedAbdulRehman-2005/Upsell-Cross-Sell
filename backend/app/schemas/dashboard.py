"""Pydantic v2 schemas for Stage 7 Frontend dashboard, catalogue, order, and customer views."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.whatsapp import CampaignSummarySchema


class DashboardOverviewMetrics(BaseModel):
    """Aggregate KPIs for the Overview dashboard."""

    model_config = ConfigDict(extra="ignore")

    total_campaigns: int = 0
    ready_for_dispatch: int = 0
    sent_simulated: int = 0
    provider_accepted: int = 0
    delivered: int = 0
    read: int = 0
    suppressed: int = 0
    failed_retryable: int = 0
    failed_permanent: int = 0
    delivery_unknown: int = 0

    total_orders: int = 0
    delivered_orders: int = 0
    total_customers: int = 0
    opted_in_customers: int = 0
    opted_out_customers: int = 0
    total_products: int = 0

    active_provider: str
    is_provider_configured: bool
    recent_campaigns: list[CampaignSummarySchema] = Field(default_factory=list)


class CustomerListViewItem(BaseModel):
    """Customer item for Orders & Customers dashboard table."""

    model_config = ConfigDict(extra="ignore")

    id: int
    external_customer_id: str
    full_name: str | None = None
    masked_phone: str
    whatsapp_opt_in: bool | None = None
    opted_out: bool | None = None
    consent_source: str | None = None
    consent_recorded_at: datetime | None = None
    order_count: int = 0
    campaign_count: int = 0
    created_at: datetime


class OrderListViewItem(BaseModel):
    """Order item for Orders & Customers dashboard table."""

    model_config = ConfigDict(extra="ignore")

    id: int
    external_order_id: str
    customer_id: int
    customer_external_id: str
    customer_name: str | None = None
    order_status: str
    delivered_at: datetime | None = None
    line_item_count: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)
    campaign_id: int | None = None
    campaign_status: str | None = None
    created_at: datetime


class ProductCatalogueItem(BaseModel):
    """Product summary for Product Catalogue view."""

    model_config = ConfigDict(extra="ignore")

    id: int
    product_code: str
    product_name: str
    unit_price: float | None = None
    currency: str = "INR"
    is_rx: bool | None = None
    is_active: bool | None = None
    available_quantity: int | None = None
    metadata_source: str = "WORKBOOK_ONLY"
    is_synthetic_metadata: bool = False
    mapped_recommendations_count: int = 0


class ProductMappingDetailItem(BaseModel):
    """Ranked product mapping item within a product detail view."""

    model_config = ConfigDict(extra="ignore")

    rank: int
    recommended_product_code: str
    recommended_product_name: str
    unit_price: float | None = None
    is_rx: bool | None = None
    is_active: bool | None = None
    available_quantity: int | None = None
    metadata_source: str | None = None
    is_synthetic_metadata: bool = False


class ProductDetailView(ProductCatalogueItem):
    """Full product detail with all 6 ranked product mappings."""

    mappings: list[ProductMappingDetailItem] = Field(default_factory=list)
