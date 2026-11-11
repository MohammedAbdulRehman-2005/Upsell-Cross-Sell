"""Order and OrderLineItem ORM models."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import OrderStatus

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.message import MessageLog


class Order(TimestampMixin, Base):
    """Customer order header record."""

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_customer_status", "customer_id", "order_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_order_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    order_status: Mapped[OrderStatus] = mapped_column(
        String(32),
        default=OrderStatus.PENDING,
        server_default=OrderStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    customer: Mapped[Customer] = relationship("Customer", back_populates="orders")
    line_items: Mapped[list[OrderLineItem]] = relationship(
        "OrderLineItem",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLineItem.line_index",
    )
    message_logs: Mapped[list[MessageLog]] = relationship(
        "MessageLog",
        back_populates="order",
    )


class OrderLineItem(TimestampMixin, Base):
    """Individual line item within an order.

    Note: Product code is stored as a string without a strict foreign key to
    `products.product_code` because customer orders in production may contain
    SKUs outside the 70 mapped products (the 1,500-SKU broader catalogue, tested
    in T13/T14).
    """

    __tablename__ = "order_line_items"
    __table_args__ = (
        CheckConstraint("line_index >= 0", name="order_line_items_line_index_non_negative"),
        CheckConstraint("quantity >= 1", name="order_line_items_quantity_positive"),
        UniqueConstraint("order_id", "line_index", name="uq_order_line_items_order_line_index"),
        Index("ix_order_line_items_order_id", "order_id"),
        Index("ix_order_line_items_product_code", "product_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        nullable=False,
    )

    order: Mapped[Order] = relationship("Order", back_populates="line_items")
