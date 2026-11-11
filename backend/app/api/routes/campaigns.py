"""Stage 6: campaign, dispatch and simulator API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.repositories.message_repository import MessageLogRepository
from app.repositories.simulator_repository import SimulatorMessageRepository
from app.schemas.whatsapp import (
    CampaignDetailSchema,
    CampaignDispatchRequest,
    CampaignDispatchResponse,
    CampaignSummarySchema,
    SimulatorDemoSeedResponseSchema,
    SimulatorInteractionRequest,
    SimulatorInteractionResponse,
    SimulatorMessageSchema,
    WhatsAppProviderStatusSchema,
)
from app.services.whatsapp import (
    DispatchCoordinator,
    SimulatorInteractionService,
    get_whatsapp_provider,
    serialize_campaign_detail,
    serialize_campaign_summary,
)

router = APIRouter(prefix="/api/v1", tags=["campaigns"])


def _settings(request: Request) -> Settings:
    s = getattr(request.app.state, "settings", None)
    return s if isinstance(s, Settings) else get_settings()


@router.get("/campaigns/provider-status", response_model=WhatsAppProviderStatusSchema)
def provider_status(settings: Settings = Depends(_settings)) -> WhatsAppProviderStatusSchema:
    provider = get_whatsapp_provider(settings=settings)
    cfg = provider.validate_configuration()
    return WhatsAppProviderStatusSchema(
        configured_provider=settings.whatsapp_provider,
        active_provider=provider.provider_type.value,
        is_configured=cfg.is_valid,
        missing_settings=cfg.missing_settings,
        template_name=settings.whatsapp_template_name,
        template_language=settings.whatsapp_template_language,
        api_version=settings.whatsapp_api_version,
        http_timeout_seconds=settings.whatsapp_http_timeout_seconds,
        webhook_verify_token_configured=bool(settings.whatsapp_webhook_verify_token.get_secret_value()),
        webhook_app_secret_configured=bool(settings.whatsapp_app_secret.get_secret_value()),
        notes=(
            cfg.message
            + " Provider acceptance is not delivery; DELIVERED is set only from verified Meta webhooks."
            if settings.whatsapp_provider == "meta_cloud_api"
            else "SIMULATOR MODE: messages are simulated and never delivered to a real phone."
        ),
    )


@router.get("/campaigns", response_model=list[CampaignSummarySchema])
def list_campaigns(session: Session = Depends(get_db)) -> list[CampaignSummarySchema]:
    return [
        serialize_campaign_summary(session, c)
        for c in MessageLogRepository(session).list_all_campaigns()
    ]


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetailSchema)
def get_campaign(campaign_id: int, session: Session = Depends(get_db)) -> CampaignDetailSchema:
    campaign = MessageLogRepository(session).get_by_id(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found.")
    return serialize_campaign_detail(session, campaign)


@router.post("/campaigns/{campaign_id}/dispatch", response_model=CampaignDispatchResponse)
def dispatch_campaign(
    campaign_id: int,
    body: CampaignDispatchRequest | None = None,
    settings: Settings = Depends(_settings),
    session: Session = Depends(get_db),
) -> CampaignDispatchResponse:
    try:
        return DispatchCoordinator(session, settings=settings).dispatch_campaign(
            campaign_id, force_retry=bool(body and body.force_retry)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/simulator/campaigns/{campaign_id}/messages",
    response_model=list[SimulatorMessageSchema],
)
def simulator_messages(
    campaign_id: int, session: Session = Depends(get_db)
) -> list[SimulatorMessageSchema]:
    if MessageLogRepository(session).get_by_id(campaign_id) is None:
        raise HTTPException(status_code=404, detail=f"Campaign {campaign_id} not found.")
    return [
        SimulatorMessageSchema.model_validate(m)
        for m in SimulatorMessageRepository(session).list_by_message_log_id(campaign_id)
    ]


@router.post(
    "/simulator/campaigns/{campaign_id}/interact",
    response_model=SimulatorInteractionResponse,
)
def simulator_interact(
    campaign_id: int,
    body: SimulatorInteractionRequest,
    settings: Settings = Depends(_settings),
    session: Session = Depends(get_db),
) -> SimulatorInteractionResponse:
    try:
        return SimulatorInteractionService(session, settings=settings).record_customer_interaction(
            campaign_id, body
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/simulator/demo-seed", response_model=SimulatorDemoSeedResponseSchema)
def demo_seed(
    settings: Settings = Depends(_settings),
    session: Session = Depends(get_db),
) -> SimulatorDemoSeedResponseSchema:
    if settings.environment in ("production", "staging"):
        raise HTTPException(status_code=403, detail="Demo seeding is disabled in this environment.")
    return SimulatorInteractionService(session, settings=settings).seed_demo_campaigns()
