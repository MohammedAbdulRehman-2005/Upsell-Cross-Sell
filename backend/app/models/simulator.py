"""SimulatorMessage ORM model for interactive WhatsApp simulator conversations."""
from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import SimulatorMessageDirection


class SimulatorMessage(TimestampMixin, Base):
    """Persisted simulated WhatsApp message (outbound template or inbound customer interaction).

    Every record is explicitly tagged `is_simulated=True` and never represents
    confirmed Meta WhatsApp Cloud API delivery.
    """

    __tablename__ = "simulator_messages"
    __table_args__ = (
        Index("ix_simulator_messages_campaign_chronological", "message_log_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulated_message_id: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    message_log_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("message_logs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    direction: Mapped[SimulatorMessageDirection] = mapped_column(
        String(32),
        nullable=False,
    )
    message_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="TEMPLATE_RECOMMENDATION",
        server_default="TEMPLATE_RECOMMENDATION",
    )
    is_simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    recipient_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    recommendations_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    action_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    selected_product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
