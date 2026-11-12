"""Stage 8: WhatsApp message entity for inbound and outbound messages."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.customer import Customer


class WhatsAppMessage(TimestampMixin, Base):
    """Persisted log of real or simulated WhatsApp inbound/outbound messages."""

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        Index("ix_whatsapp_messages_provider_msg_id", "provider_message_id", unique=True),
        Index("ix_whatsapp_messages_phone_number", "phone_number"),
        Index("ix_whatsapp_messages_customer_id", "customer_id"),
        Index("ix_whatsapp_messages_direction", "direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_message_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        default="META_CLOUD_API",
        server_default="META_CLOUD_API",
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )  # INBOUND or OUTBOUND
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    phone_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    message_type: Mapped[str] = mapped_column(
        String(32),
        default="text",
        server_default="text",
        nullable=False,
    )
    body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    is_simulated: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )

    customer: Mapped[Customer | None] = relationship("Customer")
