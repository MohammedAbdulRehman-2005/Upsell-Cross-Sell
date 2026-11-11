"""MessageLog ORM model for outbound WhatsApp dispatch and delivery tracking."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import MessageDeliveryStatus, MessagingProvider

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.order import Order


class MessageLog(TimestampMixin, Base):
    """Audit and idempotency record for outbound WhatsApp recommendation messages.

    Separates `PROVIDER_ACCEPTED` (HTTP acceptance by Meta Cloud API) from
    `DELIVERED` (confirmed via Meta webhook status update), and ensures one
    logical dispatch per `(order_id, campaign_type)`.
    """

    __tablename__ = "message_logs"
    __table_args__ = (
        CheckConstraint("retry_count >= 0", name="message_logs_retry_count_non_negative"),
        UniqueConstraint(
            "order_id",
            "campaign_type",
            name="uq_message_logs_order_campaign",
        ),
        Index("ix_message_logs_provider_message_id", "provider_message_id"),
        Index("ix_message_logs_delivery_status", "delivery_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    campaign_type: Mapped[str] = mapped_column(
        String(64),
        default="POST_DELIVERY_UPSELL",
        server_default="POST_DELIVERY_UPSELL",
        nullable=False,
    )
    trigger_event_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    recipient_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[MessagingProvider | None] = mapped_column(String(32), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_status: Mapped[MessageDeliveryStatus] = mapped_column(
        String(32),
        default=MessageDeliveryStatus.PENDING,
        server_default=MessageDeliveryStatus.PENDING.value,
        nullable=False,
    )
    recommendation_status: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    suppression_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    recommended_skus: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    decision_trace: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provider_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    order: Mapped[Order] = relationship("Order", back_populates="message_logs")
    customer: Mapped[Customer] = relationship("Customer", back_populates="message_logs")
