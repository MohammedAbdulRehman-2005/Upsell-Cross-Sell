"""MessageLog repository for clean database access."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MessageDeliveryStatus, MessagingProvider
from app.models.message import MessageLog


class MessageLogRepository:
    """Data access layer for MessageLog records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, message_id: int) -> MessageLog | None:
        return self.session.get(MessageLog, message_id)

    def get_by_id_for_update(self, message_id: int) -> MessageLog | None:
        stmt = (
            select(MessageLog)
            .where(MessageLog.id == message_id)
            .with_for_update()
        )
        return self.session.scalar(stmt)

    def list_all_campaigns(self) -> list[MessageLog]:
        stmt = select(MessageLog).order_by(MessageLog.created_at.desc(), MessageLog.id.desc())
        return list(self.session.scalars(stmt).all())

    def get_by_idempotency_key(self, idempotency_key: str) -> MessageLog | None:
        stmt = select(MessageLog).where(MessageLog.idempotency_key == idempotency_key)
        return self.session.scalar(stmt)

    def get_by_order_and_campaign(
        self,
        order_id: int,
        campaign_type: str = "POST_DELIVERY_UPSELL",
    ) -> MessageLog | None:
        stmt = select(MessageLog).where(
            MessageLog.order_id == order_id,
            MessageLog.campaign_type == campaign_type,
        )
        return self.session.scalar(stmt)

    def get_by_provider_message_id(self, provider_message_id: str) -> MessageLog | None:
        stmt = select(MessageLog).where(MessageLog.provider_message_id == provider_message_id)
        return self.session.scalar(stmt)

    def get_by_provider_message_id_for_update(self, provider_message_id: str) -> MessageLog | None:
        stmt = (
            select(MessageLog)
            .where(MessageLog.provider_message_id == provider_message_id)
            .with_for_update()
        )
        return self.session.scalar(stmt)

    def create(
        self,
        *,
        idempotency_key: str,
        order_id: int,
        customer_id: int,
        recipient_phone: str | None = None,
        provider: MessagingProvider | None = None,
        campaign_type: str = "POST_DELIVERY_UPSELL",
        trigger_event_id: str | None = None,
        delivery_status: MessageDeliveryStatus = MessageDeliveryStatus.PENDING,
        recommendation_status: str | None = None,
        suppression_reason: str | None = None,
        recommended_skus: list[str] | None = None,
        decision_trace: dict[str, Any] | None = None,
        metadata_provenance: dict[str, Any] | None = None,
        request_payload: dict[str, Any] | None = None,
        error_detail: str | None = None,
        commit: bool = True,
    ) -> MessageLog:
        record = MessageLog(
            idempotency_key=idempotency_key,
            order_id=order_id,
            customer_id=customer_id,
            campaign_type=campaign_type,
            trigger_event_id=trigger_event_id,
            recipient_phone=recipient_phone,
            provider=provider,
            delivery_status=delivery_status,
            recommendation_status=recommendation_status,
            suppression_reason=suppression_reason,
            recommended_skus=recommended_skus or [],
            decision_trace=decision_trace,
            metadata_provenance=metadata_provenance,
            request_payload=request_payload,
            error_detail=error_detail,
        )
        self.session.add(record)
        if commit:
            self.session.commit()
            self.session.refresh(record)
        else:
            self.session.flush()
        return record

    def update_delivery_state(
        self,
        record: MessageLog,
        *,
        delivery_status: MessageDeliveryStatus,
        provider: MessagingProvider | None = None,
        provider_message_id: str | None = None,
        request_payload: dict[str, Any] | None = None,
        response_payload: dict[str, Any] | None = None,
        provider_error_code: str | None = None,
        provider_error_message: str | None = None,
        error_detail: str | None = None,
        retry_count: int | None = None,
        dispatch_started_at: datetime | None = None,
        provider_accepted_at: datetime | None = None,
        delivered_at: datetime | None = None,
        read_at: datetime | None = None,
        clear_errors: bool = False,
        commit: bool = True,
    ) -> MessageLog:
        record.delivery_status = delivery_status
        if provider is not None:
            record.provider = provider
        if provider_message_id is not None:
            record.provider_message_id = provider_message_id
        if request_payload is not None:
            record.request_payload = request_payload
        if response_payload is not None:
            record.response_payload = response_payload
        if clear_errors:
            record.provider_error_code = None
            record.provider_error_message = None
            record.error_detail = None
        else:
            if provider_error_code is not None:
                record.provider_error_code = provider_error_code
            if provider_error_message is not None:
                record.provider_error_message = provider_error_message
            if error_detail is not None:
                record.error_detail = error_detail
        if retry_count is not None:
            record.retry_count = retry_count
        if dispatch_started_at is not None:
            record.dispatch_started_at = dispatch_started_at
        if provider_accepted_at is not None:
            record.provider_accepted_at = provider_accepted_at
        if delivered_at is not None:
            record.delivered_at = delivered_at
        if read_at is not None:
            record.read_at = read_at
        self.session.add(record)
        if commit:
            self.session.commit()
            self.session.refresh(record)
        else:
            self.session.flush()
        return record
