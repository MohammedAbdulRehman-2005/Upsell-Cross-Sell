"""Pydantic v2 schemas for Stage 6 WhatsApp provider dispatch, webhooks, and simulator."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    MessageDeliveryStatus,
    MessagingProvider,
    SimulatorCustomerAction,
    SimulatorMessageDirection,
)


class RecommendationCardSchema(BaseModel):
    """Rendered product recommendation card for WhatsApp/Simulator display."""

    model_config = ConfigDict(extra="ignore")

    slot_number: int
    product_code: str
    product_name: str
    category: str | None = None
    unit_price: float | None = None
    source_product_code: str | None = None
    source_rank: int | None = None
    metadata_source: str | None = None


class SimulatorMessageSchema(BaseModel):
    """Read schema for a simulated WhatsApp conversation message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    simulated_message_id: str
    message_log_id: int
    order_id: int
    customer_id: int
    recipient_phone: str | None = None
    direction: SimulatorMessageDirection | str
    message_type: str
    body_text: str
    recommendations_payload: list[dict[str, Any]] | None = None
    action_type: str | None = None
    selected_product_code: str | None = None
    metadata_json: dict[str, Any] | None = None
    is_simulated: bool = True
    created_at: datetime


class CampaignSummarySchema(BaseModel):
    """Summary schema for campaign list view in the simulator UI."""

    model_config = ConfigDict(extra="ignore")

    campaign_id: int
    idempotency_key: str
    order_id: int
    external_order_id: str
    order_number: str | None = None
    customer_id: int
    external_customer_id: str
    recipient_phone: str | None = None
    provider: MessagingProvider | str | None = None
    delivery_status: MessageDeliveryStatus | str
    recommendation_status: str | None = None
    suppression_reason: str | None = None
    recommended_skus: list[str] = Field(default_factory=list)
    provider_message_id: str | None = None
    provider_error_code: str | None = None
    provider_error_message: str | None = None
    retry_count: int = 0
    is_simulated: bool = False
    dispatch_started_at: datetime | None = None
    provider_accepted_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CampaignDetailSchema(CampaignSummarySchema):
    """Full campaign detail schema including recommendation cards, decision trace, and conversation."""

    source_items: list[dict[str, Any]] = Field(default_factory=list)
    recommendation_cards: list[RecommendationCardSchema] = Field(default_factory=list)
    decision_trace: dict[str, Any] | None = None
    metadata_provenance: dict[str, Any] | None = None
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None
    error_detail: str | None = None
    simulator_messages: list[SimulatorMessageSchema] = Field(default_factory=list)


class CampaignDispatchRequest(BaseModel):
    """Optional override parameters when triggering campaign dispatch."""

    model_config = ConfigDict(extra="forbid")

    force_retry: bool = Field(
        default=False,
        description="Allow retrying a FAILED_RETRYABLE campaign.",
    )


class CampaignDispatchResponse(BaseModel):
    """Structured result returned by the DispatchCoordinator."""

    model_config = ConfigDict(extra="ignore")

    campaign_id: int
    order_id: int
    external_order_id: str | None = None
    provider: str
    previous_status: str
    delivery_status: str
    dispatch_action: str
    provider_message_id: str | None = None
    provider_error_code: str | None = None
    provider_error_message: str | None = None
    is_simulated: bool = False
    retry_count: int = 0
    simulated_message: SimulatorMessageSchema | None = None
    detail: str


class SimulatorInteractionRequest(BaseModel):
    """Customer interaction request inside the interactive simulator."""

    model_config = ConfigDict(extra="forbid")

    action_type: SimulatorCustomerAction
    product_code: str | None = Field(
        default=None,
        max_length=64,
        description="Target recommended product SKU for product-specific actions.",
    )
    customer_note: str | None = Field(
        default=None,
        max_length=500,
        description="Optional custom note from the simulated customer.",
    )


class SimulatorInteractionResponse(BaseModel):
    """Response returned after recording a simulated customer interaction."""

    model_config = ConfigDict(extra="ignore")

    campaign_id: int
    action_type: str
    selected_product_code: str | None = None
    inbound_message: SimulatorMessageSchema
    outbound_reply: SimulatorMessageSchema
    all_messages: list[SimulatorMessageSchema]


class WhatsAppProviderStatusSchema(BaseModel):
    """Current WhatsApp provider mode and configuration health."""

    model_config = ConfigDict(extra="ignore")

    configured_provider: str
    active_provider: str
    is_configured: bool
    missing_settings: list[str] = Field(default_factory=list)
    template_name: str
    template_language: str
    api_version: str
    http_timeout_seconds: float
    webhook_verify_token_configured: bool
    webhook_app_secret_configured: bool
    notes: str


class MetaStatusWebhookResponseSchema(BaseModel):
    """Structured result from processing a Meta WhatsApp status webhook payload."""

    model_config = ConfigDict(extra="ignore")

    status: str
    events_received: int = 0
    campaigns_updated: int = 0
    duplicates_ignored: int = 0
    unknown_messages_ignored: int = 0
    regressed_statuses_ignored: int = 0
    details: list[dict[str, Any]] = Field(default_factory=list)


class SimulatorDemoSeedResponseSchema(BaseModel):
    """Response when seeding or refreshing demo campaigns for the simulator."""

    model_config = ConfigDict(extra="ignore")

    status: str
    campaigns_ready: list[int] = Field(default_factory=list)
    campaigns_dispatched_simulated: list[int] = Field(default_factory=list)
    campaigns_suppressed: list[int] = Field(default_factory=list)
    detail: str
