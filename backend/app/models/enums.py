"""Domain enumeration types shared across models and schemas."""
from __future__ import annotations

from enum import StrEnum


class CatalogueMetadataSource(StrEnum):
    """Provenance of a product's catalogue eligibility metadata (Rx, stock, active, price)."""

    WORKBOOK_ONLY = "WORKBOOK_ONLY"
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    VERIFIED_CATALOGUE = "VERIFIED_CATALOGUE"


class OrderStatus(StrEnum):
    """Lifecycle states of a customer order."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class WebhookEventType(StrEnum):
    """Supported inbound webhook event types."""

    ORDER_DELIVERED = "ORDER_DELIVERED"
    ORDER_STATUS_UPDATED = "ORDER_STATUS_UPDATED"
    WHATSAPP_STATUS_UPDATE = "WHATSAPP_STATUS_UPDATE"


class WebhookProcessingStatus(StrEnum):
    """Processing status of an inbound webhook event record."""

    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"


class MessagingProvider(StrEnum):
    """Outbound WhatsApp messaging providers."""

    META_CLOUD_API = "META_CLOUD_API"
    WHATSAPP_SIMULATOR = "WHATSAPP_SIMULATOR"


class MessageDeliveryStatus(StrEnum):
    """Outbound WhatsApp campaign and message dispatch/delivery states."""

    PENDING = "PENDING"
    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
    DISPATCHING = "DISPATCHING"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    SENT_SIMULATED = "SENT_SIMULATED"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    SUPPRESSED = "SUPPRESSED"


class SimulatorMessageDirection(StrEnum):
    """Direction of a message in the interactive WhatsApp simulator."""

    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"


class SimulatorCustomerAction(StrEnum):
    """Supported interactive customer actions in the WhatsApp simulator."""

    VIEW_RECOMMENDATION = "VIEW_RECOMMENDATION"
    INTERESTED = "INTERESTED"
    NOT_INTERESTED = "NOT_INTERESTED"
    VIEW_ALL_RECOMMENDATIONS = "VIEW_ALL_RECOMMENDATIONS"


class CampaignSuppressionReason(StrEnum):
    """Canonical suppression and terminal rejection reasons for Stage 5 campaign orchestration."""

    CUSTOMER_PHONE_MISSING = "CUSTOMER_PHONE_MISSING"
    INVALID_WHATSAPP_DESTINATION = "INVALID_WHATSAPP_DESTINATION"
    WHATSAPP_MARKETING_CONSENT_MISSING = "WHATSAPP_MARKETING_CONSENT_MISSING"
    CUSTOMER_OPTED_OUT = "CUSTOMER_OPTED_OUT"
    CONSENT_STATUS_UNKNOWN = "CONSENT_STATUS_UNKNOWN"
    NO_ELIGIBLE_RECOMMENDATIONS = "NO_ELIGIBLE_RECOMMENDATIONS"
    NO_MAPPED_SOURCE_PRODUCTS = "NO_MAPPED_SOURCE_PRODUCTS"
    INVALID_PERSISTED_ORDER_DATA = "INVALID_PERSISTED_ORDER_DATA"


class DeliveryEventOutcome(StrEnum):
    """High-level orchestration outcome returned when processing a delivery event."""

    CAMPAIGN_READY_FOR_DISPATCH = "CAMPAIGN_READY_FOR_DISPATCH"
    CAMPAIGN_SUPPRESSED = "CAMPAIGN_SUPPRESSED"
    DUPLICATE_EVENT_NOOP = "DUPLICATE_EVENT_NOOP"
    DUPLICATE_ORDER_CAMPAIGN_NOOP = "DUPLICATE_ORDER_CAMPAIGN_NOOP"
    REJECTED_UNAUTHORIZED_SOURCE = "REJECTED_UNAUTHORIZED_SOURCE"
    REJECTED_UNSUPPORTED_EVENT_TYPE = "REJECTED_UNSUPPORTED_EVENT_TYPE"
    REJECTED_ORDER_NOT_FOUND = "REJECTED_ORDER_NOT_FOUND"
    REJECTED_ORDER_CANCELLED = "REJECTED_ORDER_CANCELLED"
    REJECTED_ORDER_NOT_DELIVERED = "REJECTED_ORDER_NOT_DELIVERED"
    REJECTED_ORDER_CUSTOMER_MISMATCH = "REJECTED_ORDER_CUSTOMER_MISMATCH"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
