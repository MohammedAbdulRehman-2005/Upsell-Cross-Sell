"""WebhookEvent ORM model for inbound delivery and provider status events."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import WebhookEventType, WebhookProcessingStatus


class WebhookEvent(TimestampMixin, Base):
    """Stores inbound order-delivery and WhatsApp status webhook payloads for idempotency and audit."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("ix_webhook_events_order_event_type", "external_order_id", "event_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    event_type: Mapped[WebhookEventType] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    external_order_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    source: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    processing_status: Mapped[WebhookProcessingStatus] = mapped_column(
        String(32),
        default=WebhookProcessingStatus.RECEIVED,
        server_default=WebhookProcessingStatus.RECEIVED.value,
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
