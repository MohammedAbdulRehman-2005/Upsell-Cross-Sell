"""Stage 8: Comprehensive test suite for Storefront, Demo Delivery, and Meta WhatsApp journey."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.demo_delivery import DemoDeliveryEvent
from app.models.enums import CampaignSuppressionReason, DeliveryEventOutcome, MessageDeliveryStatus, OrderStatus
from app.models.message import MessageLog
from app.models.order import Order, OrderLineItem
from app.models.product import Product, ProductMapping
from app.models.whatsapp_message import WhatsAppMessage
from app.schemas.storefront import StorefrontCheckoutRequest
from app.services.demo_delivery_service import DemoDeliveryService
from app.services.whatsapp.webhook_handler import MetaWebhookService


def _ensure_products(session: Session) -> tuple[Product, Product]:
    p1 = session.query(Product).filter_by(product_code="SRC-S8-01").first()
    if p1 is None:
        p1 = Product(
            product_code="SRC-S8-01",
            product_name="Digital Blood Pressure Monitor",
            unit_price=Decimal("1500.00"),
            is_rx=False,
            is_active=True,
            available_quantity=50,
        )
        session.add(p1)

    p2 = session.query(Product).filter_by(product_code="TGT-S8-01").first()
    if p2 is None:
        p2 = Product(
            product_code="TGT-S8-01",
            product_name="BP Cuff Large Size",
            unit_price=Decimal("450.00"),
            is_rx=False,
            is_active=True,
            available_quantity=100,
        )
        session.add(p2)

    mapping = (
        session.query(ProductMapping)
        .filter_by(source_product_code="SRC-S8-01", recommended_product_code="TGT-S8-01")
        .first()
    )
    if mapping is None:
        mapping = ProductMapping(
            source_product_code="SRC-S8-01",
            recommended_product_code="TGT-S8-01",
            recommendation_rank=1,
        )
        session.add(mapping)

    session.commit()
    return p1, p2


def test_storefront_cart_validation_and_product_check(client: TestClient, db_session: Session):
    _ensure_products(db_session)

    # 1. Empty cart rejected
    res = client.post(
        "/api/v1/store/orders",
        json={
            "customer_name": "Test User",
            "customer_phone": "+919876543210",
            "delivery_address": "Test Street 123",
            "items": [],
            "whatsapp_opt_in": True,
        },
    )
    assert res.status_code == 422

    # 2. Nonexistent product code rejected
    res = client.post(
        "/api/v1/store/orders",
        json={
            "customer_name": "Test User",
            "customer_phone": "+919876543210",
            "delivery_address": "Test Street 123",
            "items": [{"product_code": "NON_EXISTENT_SKU", "quantity": 1}],
            "whatsapp_opt_in": True,
        },
    )
    assert res.status_code == 422
    assert "does not exist in the catalogue" in res.text

    # 3. Invalid phone number (missing +, invalid characters, too short)
    for bad_phone in ["9876543210", "+0123456", "+12345", "not-a-number", ""]:
        res = client.post(
            "/api/v1/store/orders",
            json={
                "customer_name": "Test User",
                "customer_phone": bad_phone,
                "delivery_address": "Test Street 123",
                "items": [{"product_code": "SRC-S8-01", "quantity": 1}],
                "whatsapp_opt_in": True,
            },
        )
        assert res.status_code == 422


def test_storefront_order_creation_with_consent(client: TestClient, db_session: Session):
    _ensure_products(db_session)
    phone = "+919111122223"

    res = client.post(
        "/api/v1/store/orders",
        json={
            "customer_name": "Aarav Sharma",
            "customer_phone": phone,
            "delivery_address": "402 Palm Heights, Bangalore",
            "items": [{"product_code": "SRC-S8-01", "quantity": 2}],
            "whatsapp_opt_in": True,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["order_status"] == OrderStatus.PLACED.value
    assert data["whatsapp_opt_in"] is True
    assert data["is_demo_order"] is True
    assert data["total_amount"] == 3000.0
    assert "tracking_url" in data
    assert data["estimated_delivery_seconds"] == 12.0

    # Verify DB persistence
    cust = db_session.query(Customer).filter_by(phone_number=phone).first()
    assert cust is not None
    assert cust.whatsapp_opt_in is True
    assert cust.opted_out is False
    assert cust.consent_source == "STOREFRONT_CHECKOUT"
    assert cust.consent_recorded_at is not None

    order = db_session.query(Order).filter_by(id=data["order_id"]).first()
    assert order is not None
    assert order.order_source == "STOREFRONT"
    assert order.is_demo_order is True
    assert order.delivery_address == "402 Palm Heights, Bangalore"

    demo_event = db_session.query(DemoDeliveryEvent).filter_by(order_id=order.id).first()
    assert demo_event is not None
    assert demo_event.status == "SCHEDULED"
    assert demo_event.step_timestamps.get(OrderStatus.PLACED.value) is not None


def test_storefront_order_creation_without_consent(client: TestClient, db_session: Session):
    _ensure_products(db_session)
    phone = "+919333344445"

    res = client.post(
        "/api/v1/store/orders",
        json={
            "customer_name": "Priya Patel",
            "customer_phone": phone,
            "delivery_address": "12 Lotus Lane, Mumbai",
            "items": [{"product_code": "SRC-S8-01", "quantity": 1}],
            "whatsapp_opt_in": False,
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["whatsapp_opt_in"] is False

    cust = db_session.query(Customer).filter_by(phone_number=phone).first()
    assert cust is not None
    assert cust.whatsapp_opt_in is False
    assert cust.consent_source is None


def test_demo_delivery_progression_and_campaign_trigger(client: TestClient, db_session: Session):
    _ensure_products(db_session)
    phone = "+919555566667"

    res = client.post(
        "/api/v1/store/orders",
        json={
            "customer_name": "Karan Mehra",
            "customer_phone": phone,
            "delivery_address": "88 Koramangala, Bangalore",
            "items": [{"product_code": "SRC-S8-01", "quantity": 1}],
            "whatsapp_opt_in": True,
        },
    )
    assert res.status_code == 201
    order_id = res.json()["order_id"]
    ext_order_id = res.json()["external_order_id"]

    service = DemoDeliveryService(db_session)
    demo_event = db_session.query(DemoDeliveryEvent).filter_by(order_id=order_id).first()

    # Fast forward scheduled target to simulate 12s elapsed
    demo_event.target_delivery_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    # Advance demo delivery
    did_advance = service.advance_demo_delivery_if_due(order_id)
    assert did_advance is True

    # Verify order is now DELIVERED
    order = db_session.query(Order).filter_by(id=order_id).first()
    assert order.order_status == OrderStatus.DELIVERED
    assert order.delivered_at is not None

    # Verify Stage 5 Campaign was created
    campaign = db_session.query(MessageLog).filter_by(order_id=order.id).first()
    assert campaign is not None
    assert campaign.recipient_phone == phone
    assert campaign.delivery_status in (
        MessageDeliveryStatus.READY_FOR_DISPATCH,
        MessageDeliveryStatus.SENT_SIMULATED,
        MessageDeliveryStatus.PROVIDER_ACCEPTED,
        MessageDeliveryStatus.DELIVERED,
    )
    assert "TGT-S8-01" in (campaign.recommended_skus or [])

    # Query tracking endpoint
    track_res = client.get(f"/api/v1/store/orders/{ext_order_id}/tracking")
    assert track_res.status_code == 200
    track_data = track_res.json()
    assert track_data["order_status"] == OrderStatus.DELIVERED.value
    assert track_data["delivery_completed"] is True
    assert len(track_data["steps"]) == 5
    # All steps completed
    for step in track_data["steps"]:
        assert step["is_completed"] is True
    assert len(track_data["recommendations"]) >= 1


def test_inbound_whatsapp_webhook_parsing_and_deduplication(client: TestClient, db_session: Session):
    _ensure_products(db_session)
    phone = "+919777788889"
    cust = Customer(
        external_customer_id="CUST-INBOUND-TEST",
        full_name="Inbound User",
        phone_number=phone,
        whatsapp_opt_in=True,
        opted_out=False,
    )
    db_session.add(cust)
    db_session.commit()

    wamid = "wamid.HBgLMTEyMjMzNDQ1NQ=="
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_TEST_ID",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": "919777788889",
                                    "timestamp": "1731390000",
                                    "type": "text",
                                    "text": {"body": "Hello, thank you!"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    res = client.post("/api/v1/webhooks/whatsapp", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["inbound_messages_processed"] == 1

    # Verify message in DB
    msg = db_session.query(WhatsAppMessage).filter_by(provider_message_id=wamid).first()
    assert msg is not None
    assert msg.direction == "INBOUND"
    assert msg.phone_number == phone
    assert msg.body == "Hello, thank you!"
    assert msg.customer_id == cust.id

    # Replay same payload -> duplicate ignored
    res_dup = client.post("/api/v1/webhooks/whatsapp", json=payload)
    assert res_dup.status_code == 200
    # Duplicate ignored
    dup_details = res_dup.json().get("details", [])
    assert any("DUPLICATE_INBOUND_IGNORED" in d.get("outcome", "") for d in dup_details)


def test_inbound_whatsapp_stop_unsubscribes_customer(client: TestClient, db_session: Session):
    _ensure_products(db_session)
    phone = "+919888899990"
    cust = Customer(
        external_customer_id="CUST-STOP-TEST",
        full_name="Stop User",
        phone_number=phone,
        whatsapp_opt_in=True,
        opted_out=False,
    )
    db_session.add(cust)
    db_session.commit()

    wamid_stop = "wamid.STOP_12345"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "id": wamid_stop,
                                    "from": "919888899990",
                                    "type": "text",
                                    "text": {"body": "STOP"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }

    res = client.post("/api/v1/webhooks/whatsapp", json=payload)
    assert res.status_code == 200

    # Check Customer opt-out status
    db_session.refresh(cust)
    assert cust.opted_out is True
    assert cust.whatsapp_opt_in is False
    assert cust.opt_out_recorded_at is not None

    # Check outbound unsubscription acknowledgment
    outbound = (
        db_session.query(WhatsAppMessage)
        .filter(WhatsAppMessage.direction == "OUTBOUND", WhatsAppMessage.customer_id == cust.id)
        .first()
    )
    assert outbound is not None
    assert "unsubscribed" in outbound.body.lower()


def test_inbound_whatsapp_start_resubscribes_customer(client: TestClient, db_session: Session):
    _ensure_products(db_session)
    phone = "+919999900001"
    cust = Customer(
        external_customer_id="CUST-START-TEST",
        full_name="Start User",
        phone_number=phone,
        whatsapp_opt_in=False,
        opted_out=True,
    )
    db_session.add(cust)
    db_session.commit()

    wamid_start = "wamid.START_67890"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": wamid_start,
                                    "from": "919999900001",
                                    "type": "text",
                                    "text": {"body": "START"},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }

    res = client.post("/api/v1/webhooks/whatsapp", json=payload)
    assert res.status_code == 200

    db_session.refresh(cust)
    assert cust.opted_out is False
    assert cust.whatsapp_opt_in is True
    assert cust.consent_source == "WHATSAPP_INBOUND_START"
