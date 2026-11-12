"""Stage 8: Customer storefront and real-time delivery tracking endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.storefront import (
    StorefrontCheckoutRequest,
    StorefrontOrderResponse,
    StorefrontTrackingResponse,
)
from app.services.demo_delivery_service import DemoDeliveryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/store", tags=["storefront"])


def _settings(request: Request) -> Settings:
    s = getattr(request.app.state, "settings", None)
    return s if isinstance(s, Settings) else get_settings()


@router.post(
    "/orders",
    response_model=StorefrontOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a customer storefront order with optional WhatsApp consent and schedule demo delivery.",
)
def place_storefront_order(
    payload: StorefrontCheckoutRequest,
    settings: Settings = Depends(_settings),
    db: Session = Depends(get_db),
) -> StorefrontOrderResponse:
    """Validate customer cart items, capture explicit marketing consent, and persist demo order."""
    service = DemoDeliveryService(db, settings=settings)
    try:
        return service.create_order(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/orders/{order_id}/tracking",
    response_model=StorefrontTrackingResponse,
    summary="Retrieve live milestone progression, countdown, and post-delivery upsell recommendations.",
)
def get_order_tracking(
    order_id: str,
    settings: Settings = Depends(_settings),
    db: Session = Depends(get_db),
) -> StorefrontTrackingResponse:
    """Fetch real-time delivery tracking stepper and WhatsApp dispatch details."""
    service = DemoDeliveryService(db, settings=settings)
    try:
        return service.get_tracking(order_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
