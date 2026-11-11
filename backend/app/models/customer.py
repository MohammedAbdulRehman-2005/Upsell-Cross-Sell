"""Customer ORM model."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.message import MessageLog
    from app.models.order import Order


class Customer(TimestampMixin, Base):
    """Represents a customer and their WhatsApp messaging consent state."""

    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_phone_number", "phone_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_customer_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    whatsapp_opt_in: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    opted_out: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    consent_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    orders: Mapped[list[Order]] = relationship(
        "Order",
        back_populates="customer",
        cascade="all, delete-orphan",
    )
    message_logs: Mapped[list[MessageLog]] = relationship(
        "MessageLog",
        back_populates="customer",
    )
