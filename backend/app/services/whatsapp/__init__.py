"""WhatsApp provider integration, dispatch coordinator, webhook handler, and simulator services."""
from __future__ import annotations

from app.services.whatsapp.dispatcher import (
    DispatchCoordinator,
    build_recommendation_cards,
    serialize_campaign_detail,
    serialize_campaign_summary,
)
from app.services.whatsapp.models import (
    OutboundTemplateMessageRequest,
    OutboundTextMessageRequest,
    ParsedWebhookStatusUpdate,
    ProviderConfigurationStatus,
    ProviderSendResult,
    RecommendationCardItem,
)
from app.services.whatsapp.providers import (
    MetaCloudWhatsAppProvider,
    SimulatorWhatsAppProvider,
    WhatsAppProvider,
    get_whatsapp_provider,
    parse_meta_status_entries,
)
from app.services.whatsapp.simulator_service import SimulatorInteractionService
from app.services.whatsapp.webhook_handler import (
    MetaWebhookService,
    verify_meta_webhook_challenge,
    verify_meta_webhook_signature,
)

__all__ = [
    "DispatchCoordinator",
    "MetaCloudWhatsAppProvider",
    "MetaWebhookService",
    "OutboundTemplateMessageRequest",
    "OutboundTextMessageRequest",
    "ParsedWebhookStatusUpdate",
    "ProviderConfigurationStatus",
    "ProviderSendResult",
    "RecommendationCardItem",
    "SimulatorInteractionService",
    "SimulatorWhatsAppProvider",
    "WhatsAppProvider",
    "build_recommendation_cards",
    "get_whatsapp_provider",
    "parse_meta_status_entries",
    "serialize_campaign_detail",
    "serialize_campaign_summary",
    "verify_meta_webhook_challenge",
    "verify_meta_webhook_signature",
]
