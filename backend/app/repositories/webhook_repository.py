"""WebhookEvent repository for clean database access."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import WebhookEventType, WebhookProcessingStatus
from app.models.webhook import WebhookEvent


class WebhookEventRepository:
    """Data access layer for WebhookEvent records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, record_id: int) -> WebhookEvent | None:
        return self.session.get(WebhookEvent, record_id)

    def get_by_event_id(self, event_id: str) -> WebhookEvent | None:
        stmt = select(WebhookEvent).where(WebhookEvent.event_id == event_id)
        return self.session.scalar(stmt)

    def get_by_event_id_for_update(self, event_id: str) -> WebhookEvent | None:
        """Fetch a WebhookEvent by event_id with a row-level FOR UPDATE lock."""
        stmt = select(WebhookEvent).where(WebhookEvent.event_id == event_id).with_for_update()
        return self.session.scalar(stmt)

    def create(
        self,
        *,
        event_id: str,
        event_type: WebhookEventType,
        payload: dict[str, Any],
        external_order_id: str | None = None,
        source: str | None = None,
        occurred_at: datetime | None = None,
        processing_status: WebhookProcessingStatus = WebhookProcessingStatus.RECEIVED,
        error_message: str | None = None,
        processed_at: datetime | None = None,
        commit: bool = True,
    ) -> WebhookEvent:
        record = WebhookEvent(
            event_id=event_id,
            event_type=event_type,
            external_order_id=external_order_id,
            source=source,
            occurred_at=occurred_at,
            processing_status=processing_status,
            payload=payload,
            error_message=error_message,
            processed_at=processed_at,
        )
        self.session.add(record)
        if commit:
            self.session.commit()
            self.session.refresh(record)
        else:
            self.session.flush()
        return record

    def update_status(
        self,
        record: WebhookEvent,
        *,
        processing_status: WebhookProcessingStatus,
        error_message: str | None = None,
        processed_at: datetime | None = None,
        commit: bool = True,
    ) -> WebhookEvent:
        record.processing_status = processing_status
        record.error_message = error_message
        if processed_at is not None:
            record.processed_at = processed_at
        self.session.add(record)
        if commit:
            self.session.commit()
            self.session.refresh(record)
        else:
            self.session.flush()
        return record
