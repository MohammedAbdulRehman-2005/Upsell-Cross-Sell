"""Order repository for clean database access."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import OrderStatus
from app.models.order import Order, OrderLineItem
from app.schemas.order import OrderCreate


class OrderRepository:
    """Data access layer for Order and OrderLineItem records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, order_id: int) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.line_items), selectinload(Order.customer))
        )
        return self.session.scalar(stmt)

    def get_by_external_id(self, external_order_id: str) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.external_order_id == external_order_id)
            .options(selectinload(Order.line_items), selectinload(Order.customer))
        )
        return self.session.scalar(stmt)

    def get_by_external_id_for_update(self, external_order_id: str) -> Order | None:
        """Fetch an order by external_order_id with a row-level FOR UPDATE lock."""
        stmt = (
            select(Order)
            .where(Order.external_order_id == external_order_id)
            .with_for_update()
            .options(selectinload(Order.line_items), selectinload(Order.customer))
        )
        return self.session.scalar(stmt)

    def list_by_customer(self, customer_id: int) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.customer_id == customer_id)
            .options(selectinload(Order.line_items))
            .order_by(Order.id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_all(self, limit: int = 100, offset: int = 0) -> list[Order]:
        stmt = (
            select(Order)
            .options(selectinload(Order.line_items), selectinload(Order.customer))
            .order_by(Order.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())

    def create(self, payload: OrderCreate) -> Order:
        order = Order(
            external_order_id=payload.external_order_id,
            customer_id=payload.customer_id,
            order_status=payload.order_status,
            delivered_at=payload.delivered_at,
        )
        for item in sorted(payload.line_items, key=lambda x: x.line_index):
            order.line_items.append(
                OrderLineItem(
                    line_index=item.line_index,
                    product_code=item.product_code,
                    product_name=item.product_name,
                    quantity=item.quantity,
                )
            )
        self.session.add(order)
        self.session.commit()
        self.session.refresh(order)
        return order

    def update_status(
        self,
        order: Order,
        new_status: OrderStatus,
        delivered_at: datetime | None = None,
    ) -> Order:
        order.order_status = new_status
        if delivered_at is not None:
            order.delivered_at = delivered_at
        self.session.add(order)
        self.session.commit()
        self.session.refresh(order)
        return order
