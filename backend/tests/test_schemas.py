"""Tests for Pydantic v2 domain schemas and input validation rules."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.enums import OrderStatus, WebhookEventType
from app.schemas.customer import CustomerCreate
from app.schemas.order import OrderCreate, OrderDeliveryEvent, OrderLineItemCreate
from app.schemas.product import ProductCreate, ProductMappingCreate


def test_product_create_valid_and_nullable_metadata() -> None:
    product = ProductCreate(
        product_code="SAMPLE-001",
        product_name="Sample Gentle Face Cleanser",
        is_rx=False,
        is_active=True,
        available_quantity=25,
        unit_price=Decimal("499.00"),
        currency="INR",
    )
    assert product.product_code == "SAMPLE-001"
    assert product.available_quantity == 25

    # Unknown catalogue metadata (None) is explicitly permitted for fail-closed handling (T12)
    unknown_meta_product = ProductCreate(
        product_code="SAMPLE-099",
        product_name="Mystery Product",
        is_rx=None,
        is_active=None,
        available_quantity=None,
    )
    assert unknown_meta_product.is_rx is None
    assert unknown_meta_product.is_active is None
    assert unknown_meta_product.available_quantity is None


def test_product_create_invalid_inputs_rejected() -> None:
    # Negative stock
    with pytest.raises(ValidationError):
        ProductCreate(
            product_code="SAMPLE-001",
            product_name="Cleanser",
            available_quantity=-1,
        )

    # Blank product code or invalid characters
    with pytest.raises(ValidationError):
        ProductCreate(
            product_code="BAD CODE!",
            product_name="Cleanser",
        )

    # Extra unknown field forbidden
    with pytest.raises(ValidationError):
        ProductCreate(
            product_code="SAMPLE-001",
            product_name="Cleanser",
            unexpected_field="nope",  # type: ignore[call-arg]
        )


def test_product_mapping_rank_and_self_mapping_validation() -> None:
    valid = ProductMappingCreate(
        source_product_code="SAMPLE-001",
        recommended_product_code="SAMPLE-002",
        recommendation_rank=1,
        source_excel_row=2,
    )
    assert valid.recommendation_rank == 1

    # Rank out of 1..6 range
    with pytest.raises(ValidationError):
        ProductMappingCreate(
            source_product_code="SAMPLE-001",
            recommended_product_code="SAMPLE-002",
            recommendation_rank=7,
        )

    # Self-recommendation rejected
    with pytest.raises(ValidationError, match="cannot recommend itself"):
        ProductMappingCreate(
            source_product_code="SAMPLE-001",
            recommended_product_code="SAMPLE-001",
            recommendation_rank=1,
        )


def test_customer_create_validation() -> None:
    customer = CustomerCreate(
        external_customer_id="CUST-1001",
        full_name="Aarav Sharma",
        phone_number="+919876543210",
        whatsapp_opt_in=True,
        opted_out=False,
    )
    assert customer.phone_number == "+919876543210"

    with pytest.raises(ValidationError):
        CustomerCreate(
            external_customer_id="CUST-1001",
            phone_number="invalid-phone",
        )

    # Missing leading '+' rejected under strict E.164 validation
    with pytest.raises(ValidationError):
        CustomerCreate(
            external_customer_id="CUST-1001",
            phone_number="919876543210",
        )


def test_order_and_delivery_event_validation() -> None:
    order = OrderCreate(
        external_order_id="ORD-5001",
        customer_id=1,
        order_status=OrderStatus.DELIVERED,
        line_items=[
            OrderLineItemCreate(line_index=0, product_code="SAMPLE-001", quantity=2),
            OrderLineItemCreate(line_index=1, product_code="SAMPLE-002", quantity=1),
        ],
    )
    assert len(order.line_items) == 2

    # Empty line items rejected
    with pytest.raises(ValidationError):
        OrderCreate(
            external_order_id="ORD-5002",
            customer_id=1,
            line_items=[],
        )

    # Duplicate line_index rejected
    with pytest.raises(ValidationError, match="Duplicate line_index"):
        OrderCreate(
            external_order_id="ORD-5003",
            customer_id=1,
            line_items=[
                OrderLineItemCreate(line_index=0, product_code="SAMPLE-001", quantity=1),
                OrderLineItemCreate(line_index=0, product_code="SAMPLE-002", quantity=1),
            ],
        )

    # Zero quantity rejected
    with pytest.raises(ValidationError):
        OrderLineItemCreate(line_index=0, product_code="SAMPLE-001", quantity=0)

    # Delivery event auto-populates sequential line_index when omitted
    event = OrderDeliveryEvent(
        event_id="evt_20261111_01",
        event_type=WebhookEventType.ORDER_DELIVERED,
        order_id="ORD-5001",
        customer_id="CUST-1001",
        customer_phone="+919876543210",
        order_status=OrderStatus.DELIVERED,
        line_items=[
            {"product_code": "SAMPLE-001", "quantity": 1},  # type: ignore[list-item]
            {"product_code": "SAMPLE-008", "quantity": 3},  # type: ignore[list-item]
        ],
    )
    assert [item.line_index for item in event.line_items] == [0, 1]
