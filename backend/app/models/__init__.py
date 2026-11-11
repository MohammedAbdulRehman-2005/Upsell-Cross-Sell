"""Export all SQLAlchemy ORM models so Alembic and metadata discovery register them."""
from __future__ import annotations

from app.models.customer import Customer
from app.models.enums import (
    CampaignSuppressionReason,
    CatalogueMetadataSource,
    DeliveryEventOutcome,
    MessageDeliveryStatus,
    MessagingProvider,
    OrderStatus,
    SimulatorCustomerAction,
    SimulatorMessageDirection,
    WebhookEventType,
    WebhookProcessingStatus,
)
from app.models.message import MessageLog
from app.models.order import Order, OrderLineItem
from app.models.product import Product, ProductMapping
from app.models.simulator import SimulatorMessage
from app.models.webhook import WebhookEvent

__all__ = [
    "CampaignSuppressionReason",
    "CatalogueMetadataSource",
    "Customer",
    "DeliveryEventOutcome",
    "MessageDeliveryStatus",
    "MessageLog",
    "MessagingProvider",
    "Order",
    "OrderLineItem",
    "OrderStatus",
    "Product",
    "ProductMapping",
    "SimulatorCustomerAction",
    "SimulatorMessage",
    "SimulatorMessageDirection",
    "WebhookEvent",
    "WebhookEventType",
    "WebhookProcessingStatus",
]
