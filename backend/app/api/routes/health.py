"""Liveness and readiness health check endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import check_database_connection, get_db
from app.schemas.health import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=LivenessResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    description="Returns HTTP 200 when the FastAPI process is running and responsive.",
)
def health_live(settings: Settings = Depends(get_settings)) -> LivenessResponse:
    """Return service liveness state without checking external dependencies."""
    return LivenessResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(timezone.utc),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "Service is not ready because the database is unreachable.",
        }
    },
    summary="Readiness probe",
    description="Verifies live database connectivity (`SELECT 1`) before reporting ready.",
)
def health_ready(
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReadinessResponse:
    """Verify database connectivity and return HTTP 200 if connected or HTTP 503 if unavailable."""
    is_connected, error_detail = check_database_connection(db)
    if not is_connected:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            service=settings.app_name,
            version=settings.app_version,
            environment=settings.environment,
            database="disconnected",
            detail=error_detail,
            timestamp=datetime.now(timezone.utc),
        )
    return ReadinessResponse(
        status="ready",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        database="connected",
        detail=None,
        timestamp=datetime.now(timezone.utc),
    )
