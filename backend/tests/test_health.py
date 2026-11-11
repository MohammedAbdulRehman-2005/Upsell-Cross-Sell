"""Tests for application startup, OpenAPI docs, and health endpoints."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import check_database_connection, get_db
from app.main import create_app


def test_app_startup_and_openapi_metadata(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Plural Upsell & Cross-Sell Service (Test)"
    assert schema["info"]["version"] == "0.2.0-test"
    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]


def test_health_live_endpoint(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "Plural Upsell & Cross-Sell Service (Test)"
    assert payload["version"] == "0.2.0-test"
    assert payload["environment"] == "testing"
    assert "timestamp" in payload


def test_health_ready_endpoint_when_database_connected(
    client: TestClient,
    db_session: Session,
) -> None:
    is_ok, err = check_database_connection(db_session)
    assert is_ok is True
    assert err is None

    response = client.get("/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["database"] == "connected"
    assert payload["detail"] is None


def test_health_ready_endpoint_when_database_unavailable(test_settings: Settings) -> None:
    app = create_app(test_settings)
    broken_session = MagicMock(spec=Session)
    broken_session.execute.side_effect = OperationalError("SELECT 1", {}, Exception("connection refused"))

    def _broken_db() -> Generator[Session, None, None]:
        yield broken_session

    app.dependency_overrides[get_db] = _broken_db
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app) as broken_client:
        response = broken_client.get("/health/ready")
        assert response.status_code == 503
        payload = response.json()
        assert payload["status"] == "not_ready"
        assert payload["database"] == "disconnected"
        assert "connection refused" in (payload["detail"] or "")
