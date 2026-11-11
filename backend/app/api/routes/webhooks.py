"""Stage 5: Order-delivery webhook API routes with configurable authentication."""
from __future__ import annotations

import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.enums import DeliveryEventOutcome
from app.schemas.campaign import OrderDeliveredWebhookRequest, OrderDeliveryWebhookResponse
from app.services.campaign_orchestrator import CampaignOrchestrationService
from app.services.whatsapp import (
    MetaWebhookService,
    verify_meta_webhook_challenge,
    verify_meta_webhook_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

HTTP_422_STATUS = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)

OUTCOME_STATUS_CODE_MAP: dict[DeliveryEventOutcome, int] = {
    DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH: status.HTTP_200_OK,
    DeliveryEventOutcome.CAMPAIGN_SUPPRESSED: status.HTTP_200_OK,
    DeliveryEventOutcome.DUPLICATE_EVENT_NOOP: status.HTTP_200_OK,
    DeliveryEventOutcome.DUPLICATE_ORDER_CAMPAIGN_NOOP: status.HTTP_200_OK,
    DeliveryEventOutcome.REJECTED_ORDER_NOT_DELIVERED: status.HTTP_200_OK,
    DeliveryEventOutcome.REJECTED_ORDER_CANCELLED: status.HTTP_200_OK,
    DeliveryEventOutcome.REJECTED_UNSUPPORTED_EVENT_TYPE: HTTP_422_STATUS,
    DeliveryEventOutcome.REJECTED_UNAUTHORIZED_SOURCE: status.HTTP_403_FORBIDDEN,
    DeliveryEventOutcome.REJECTED_ORDER_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    DeliveryEventOutcome.REJECTED_ORDER_CUSTOMER_MISMATCH: status.HTTP_409_CONFLICT,
    DeliveryEventOutcome.FAILED_PERMANENT: HTTP_422_STATUS,
    DeliveryEventOutcome.FAILED_RETRYABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_webhook_settings(request: Request) -> Settings:
    """Resolve settings attached to app state or fallback to cached settings."""
    app_settings = getattr(request.app.state, "settings", None)
    if isinstance(app_settings, Settings):
        return app_settings
    return get_settings()


def verify_order_webhook_auth(
    settings: Settings = Depends(get_webhook_settings),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
) -> None:
    """Verify inbound order-delivery webhook authentication.

    Security rules:
    - 'dev_insecure_open' is only permitted when environment is 'development' or 'testing'.
      In 'staging' or 'production', 'dev_insecure_open' fails closed.
    - 'shared_token_header' requires a configured non-empty order_webhook_shared_token
      and verifies X-Webhook-Token using constant-time hmac.compare_digest.
    """
    if settings.order_webhook_auth_mode == "dev_insecure_open":
        if settings.environment in ("production", "staging"):
            logger.error(
                "Rejected webhook request: order_webhook_auth_mode='dev_insecure_open' is forbidden in %s.",
                settings.environment,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Insecure webhook authentication mode 'dev_insecure_open' is forbidden "
                    f"in '{settings.environment}' environment."
                ),
            )
        return

    expected_token = settings.order_webhook_shared_token.get_secret_value()
    if not expected_token or not expected_token.strip():
        logger.error(
            "Webhook authentication misconfigured: order_webhook_shared_token is empty while mode='shared_token_header'."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook authentication token is not configured on the server.",
        )

    if not x_webhook_token or not hmac.compare_digest(
        x_webhook_token.encode("utf-8"),
        expected_token.encode("utf-8"),
    ):
        logger.warning("Rejected order-delivered webhook due to invalid or missing X-Webhook-Token.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Webhook-Token header.",
        )


@router.post(
    "/order-delivered",
    response_model=OrderDeliveryWebhookResponse,
    summary="Process an inbound order.delivered event and orchestrate upsell campaign preparation.",
)
def handle_order_delivered_webhook(
    payload: OrderDeliveredWebhookRequest,
    _auth: None = Depends(verify_order_webhook_auth),
    settings: Settings = Depends(get_webhook_settings),
    session: Session = Depends(get_db),
) -> JSONResponse:
    """Receive an order-delivery event, enforce idempotency and consent, and prepare campaign."""
    orchestrator = CampaignOrchestrationService(session, settings=settings)
    result = orchestrator.process_order_delivered_event(
        payload,
        raw_payload=payload.model_dump(mode="json"),
    )
    http_status = OUTCOME_STATUS_CODE_MAP.get(result.outcome, status.HTTP_200_OK)
    return JSONResponse(
        status_code=http_status,
        content=result.model_dump(mode="json"),
    )


@router.get("/whatsapp", summary="Meta WhatsApp webhook verification handshake.")
def verify_whatsapp_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    settings: Settings = Depends(get_webhook_settings),
) -> PlainTextResponse:
    try:
        challenge = verify_meta_webhook_challenge(
            hub_mode=hub_mode,
            hub_verify_token=hub_verify_token,
            hub_challenge=hub_challenge,
            settings=settings,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PlainTextResponse(challenge)


@router.post("/whatsapp", summary="Receive signed Meta WhatsApp delivery status webhooks.")
async def handle_whatsapp_status_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    settings: Settings = Depends(get_webhook_settings),
    session: Session = Depends(get_db),
) -> JSONResponse:
    raw_body = await request.body()
    if not verify_meta_webhook_signature(
        raw_body=raw_body, signature_header=x_hub_signature_256, settings=settings
    ):
        logger.warning("Rejected WhatsApp webhook: invalid or missing signature.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature.")
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON body.") from exc
    result = MetaWebhookService(session, settings=settings).process_status_webhook(payload)
    return JSONResponse(status_code=status.HTTP_200_OK, content=result.model_dump(mode="json"))
