"""SimulatorMessage repository for storing simulated WhatsApp conversations."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SimulatorMessageDirection
from app.models.simulator import SimulatorMessage


class SimulatorMessageRepository:
    """Data access layer for SimulatorMessage records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, simulator_message_id: int) -> SimulatorMessage | None:
        return self.session.get(SimulatorMessage, simulator_message_id)

    def get_by_simulated_message_id(self, simulated_message_id: str) -> SimulatorMessage | None:
        stmt = select(SimulatorMessage).where(
            SimulatorMessage.simulated_message_id == simulated_message_id
        )
        return self.session.scalar(stmt)

    def list_by_message_log_id(self, message_log_id: int) -> list[SimulatorMessage]:
        stmt = (
            select(SimulatorMessage)
            .where(SimulatorMessage.message_log_id == message_log_id)
            .order_by(SimulatorMessage.created_at.asc(), SimulatorMessage.id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def create(
        self,
        *,
        simulated_message_id: str,
        message_log_id: int,
        order_id: int,
        customer_id: int,
        recipient_phone: str,
        direction: SimulatorMessageDirection = SimulatorMessageDirection.OUTBOUND,
        message_type: str = "TEMPLATE_RECOMMENDATION",
        body_text: str,
        recommendations_payload: list[dict[str, Any]] | None = None,
        action_type: str | None = None,
        selected_product_code: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        is_simulated: bool = True,
        commit: bool = True,
    ) -> SimulatorMessage:
        record = SimulatorMessage(
            simulated_message_id=simulated_message_id,
            message_log_id=message_log_id,
            order_id=order_id,
            customer_id=customer_id,
            recipient_phone=recipient_phone,
            direction=direction,
            message_type=message_type,
            body_text=body_text,
            recommendations_payload=recommendations_payload,
            action_type=action_type,
            selected_product_code=selected_product_code,
            metadata_json=metadata_json,
            is_simulated=is_simulated,
        )
        self.session.add(record)
        if commit:
            self.session.commit()
            self.session.refresh(record)
        else:
            self.session.flush()
        return record
