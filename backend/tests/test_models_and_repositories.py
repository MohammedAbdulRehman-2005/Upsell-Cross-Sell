"""Tests for SQLAlchemy 2.x database constraints and repository operations."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import (
    MessageDeliveryStatus,
    MessagingProvider,
    OrderStatus,
    WebhookEventType,
    WebhookProcessingStatus,
)
from app.repositories import (
    CustomerRepository,
    MessageLogRepository,
    OrderRepository,
    ProductRepository,
    WebhookEventRepository,
)
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.schemas.order import OrderCreate, OrderLineItemCreate
from app.schemas.product import ProductCreate, ProductMappingCreate, ProductUpdate


def test_customer_repository_and_unique_constraints(db_session: Session) -> None:
    repo = CustomerRepository(db_session)
    created = repo.create(
        CustomerCreate(
            external_customer_id="CUST-001",
            full_name="Priya Nair",
            phone_number="+919811112222",
            whatsapp_opt_in=True,
            opted_out=False,
        )
    )
    assert created.id is not None
    assert repo.get_by_external_id("CUST-001") is not None

    updated = repo.update(created, CustomerUpdate(opted_out=True))
    assert updated.opted_out is True

    # Duplicate external_customer_id must violate unique constraint
    with pytest.raises(IntegrityError):
        repo.create(
            CustomerCreate(
                external_customer_id="CUST-001",
                full_name="Duplicate Customer",
                phone_number="+919833334444",
            )
        )
    db_session.rollback()


def test_product_and_mapping_repository_and_constraints(db_session: Session) -> None:
    repo = ProductRepository(db_session)
    p1 = repo.create(
        ProductCreate(
            product_code="SAMPLE-001",
            product_name="Sample Gentle Face Cleanser",
            is_rx=False,
            is_active=True,
            available_quantity=10,
            unit_price=Decimal("299.50"),
        )
    )
    p2 = repo.create(
        ProductCreate(
            product_code="SAMPLE-002",
            product_name="Sample Daily Moisturizer",
            is_rx=False,
            is_active=True,
            available_quantity=5,
        )
    )
    p3 = repo.create(
        ProductCreate(
            product_code="SAMPLE-003",
            product_name="Sample Hydrating Face Mist",
            is_rx=False,
            is_active=True,
            available_quantity=8,
        )
    )
    assert p1.id is not None and p2.id is not None and p3.id is not None

    updated_p1 = repo.update(p1, ProductUpdate(available_quantity=15))
    assert updated_p1.available_quantity == 15

    m1 = repo.create_mapping(
        ProductMappingCreate(
            source_product_code="SAMPLE-001",
            recommended_product_code="SAMPLE-002",
            recommendation_rank=1,
            source_excel_row=2,
        )
    )
    m2 = repo.create_mapping(
        ProductMappingCreate(
            source_product_code="SAMPLE-001",
            recommended_product_code="SAMPLE-003",
            recommendation_rank=2,
            source_excel_row=2,
        )
    )
    assert m1.id is not None and m2.id is not None

    mappings = repo.list_mappings_for_source("SAMPLE-001")
    assert [m.recommended_product_code for m in mappings] == ["SAMPLE-002", "SAMPLE-003"]

    # Duplicate rank for the same source SKU must fail unique constraint
    with pytest.raises(IntegrityError):
        repo.create_mapping(
            ProductMappingCreate(
                source_product_code="SAMPLE-001",
                recommended_product_code="SAMPLE-003",
                recommendation_rank=1,
            )
        )
    db_session.rollback()

    # Duplicate target SKU for the same source SKU must fail unique constraint
    with pytest.raises(IntegrityError):
        repo.create_mapping(
            ProductMappingCreate(
                source_product_code="SAMPLE-001",
                recommended_product_code="SAMPLE-002",
                recommendation_rank=3,
            )
        )
    db_session.rollback()


def test_order_webhook_and_message_log_repositories(db_session: Session) -> None:
    customer_repo = CustomerRepository(db_session)
    order_repo = OrderRepository(db_session)
    webhook_repo = WebhookEventRepository(db_session)
    message_repo = MessageLogRepository(db_session)

    customer = customer_repo.create(
        CustomerCreate(
            external_customer_id="CUST-200",
            full_name="Rohan Mehta",
            phone_number="+919900001111",
        )
    )

    # Order can contain both mapped SKUs and unmapped catalogue SKUs (e.g. UNMAPPED-999)
    order = order_repo.create(
        OrderCreate(
            external_order_id="ORD-9001",
            customer_id=customer.id,
            order_status=OrderStatus.SHIPPED,
            line_items=[
                OrderLineItemCreate(line_index=0, product_code="SAMPLE-001", quantity=2),
                OrderLineItemCreate(line_index=1, product_code="UNMAPPED-999", quantity=1),
            ],
        )
    )
    assert len(order.line_items) == 2

    now = datetime.now(timezone.utc)
    delivered_order = order_repo.update_status(
        order,
        OrderStatus.DELIVERED,
        delivered_at=now,
    )
    assert delivered_order.order_status == OrderStatus.DELIVERED
    assert delivered_order.delivered_at is not None

    webhook_event = webhook_repo.create(
        event_id="evt-ord-9001-delivered",
        event_type=WebhookEventType.ORDER_DELIVERED,
        external_order_id="ORD-9001",
        payload={"order_id": "ORD-9001", "status": "DELIVERED"},
    )
    assert webhook_event.processing_status == WebhookProcessingStatus.RECEIVED

    updated_event = webhook_repo.update_status(
        webhook_event,
        processing_status=WebhookProcessingStatus.PROCESSED,
        processed_at=now,
    )
    assert updated_event.processing_status == WebhookProcessingStatus.PROCESSED

    # Duplicate webhook event_id must fail unique constraint
    with pytest.raises(IntegrityError):
        webhook_repo.create(
            event_id="evt-ord-9001-delivered",
            event_type=WebhookEventType.ORDER_DELIVERED,
            external_order_id="ORD-9001",
            payload={"order_id": "ORD-9001"},
        )
    db_session.rollback()

    # MessageLog creation and state transitions (PROVIDER_ACCEPTED -> DELIVERED via webhook)
    msg_log = message_repo.create(
        idempotency_key="ORD-9001:POST_DELIVERY_UPSELL",
        order_id=order.id,
        customer_id=customer.id,
        recipient_phone=customer.phone_number,
        provider=MessagingProvider.META_CLOUD_API,
        recommended_skus=["SAMPLE-002", "SAMPLE-003"],
    )
    assert msg_log.delivery_status == MessageDeliveryStatus.PENDING

    accepted_log = message_repo.update_delivery_state(
        msg_log,
        delivery_status=MessageDeliveryStatus.PROVIDER_ACCEPTED,
        provider_message_id="wamid.HBgMOTE5OTAwMDAxMTExFQIAERgSQ0M",
        provider_accepted_at=now,
    )
    assert accepted_log.delivery_status == MessageDeliveryStatus.PROVIDER_ACCEPTED
    assert message_repo.get_by_provider_message_id("wamid.HBgMOTE5OTAwMDAxMTExFQIAERgSQ0M") is not None

    confirmed_log = message_repo.update_delivery_state(
        accepted_log,
        delivery_status=MessageDeliveryStatus.DELIVERED,
        delivered_at=now,
    )
    assert confirmed_log.delivery_status == MessageDeliveryStatus.DELIVERED

    # Second message log for same (order_id, campaign_type) must fail unique constraint (T18 idempotency foundation)
    with pytest.raises(IntegrityError):
        message_repo.create(
            idempotency_key="ORD-9001:POST_DELIVERY_UPSELL:DUP",
            order_id=order.id,
            customer_id=customer.id,
            recipient_phone=customer.phone_number,
            provider=MessagingProvider.META_CLOUD_API,
        )
    db_session.rollback()


def test_stage1_normalized_dataset_is_preserved_and_compatible(db_session: Session) -> None:
    """Verify the Stage 1 normalized JSON dataset is intact and maps cleanly to Stage 2 models."""
    mappings_path: Path = get_settings().normalized_mappings_path
    assert mappings_path.exists()
    raw = json.loads(mappings_path.read_text(encoding="utf-8"))
    assert len(raw["products"]) == 70

    repo = ProductRepository(db_session)
    # Load first isolated mapping cluster (7 products, 42 mappings) into isolated test DB
    cluster_1 = raw["products"][:7]
    for item in cluster_1:
        repo.create(
            ProductCreate(
                product_code=item["product_code"],
                product_name=item["product_name"],
                is_rx=False,
                is_active=True,
                available_quantity=10,
            )
        )
    for item in cluster_1:
        for rec in item["recommendations"]:
            repo.create_mapping(
                ProductMappingCreate(
                    source_product_code=item["product_code"],
                    recommended_product_code=rec["product_code"],
                    recommendation_rank=rec["rank"],
                    source_excel_row=item["source_excel_row"],
                )
            )

    loaded_mappings = repo.list_mappings_for_source("SAMPLE-001")
    assert len(loaded_mappings) == 6
    assert [m.recommendation_rank for m in loaded_mappings] == [1, 2, 3, 4, 5, 6]
