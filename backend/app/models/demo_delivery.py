"""Stage 8: Demo delivery event model for tracking simulated order progression."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.order import Order


class DemoDeliveryEvent(TimestampMixin, Base):
    """Tracks asynchronous demo delivery progression for storefront orders."""

    __tablename__ = "demo_delivery_events"
    __table_args__ = (
        Index("ix_demo_delivery_events_order_id", "order_id"),
        Index("ix_demo_delivery_events_target_delivery_at", "target_delivery_at"),
        Index("ix_demo_delivery_events_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="SCHEDULED",
        server_default="SCHEDULED",
        nullable=False,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    target_delivery_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    step_timestamps: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error_detail: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    order: Mapped[Order] = relationship("Order", foreign_keys=[order_id])
