"""Regression test suite for WhatsApp Inbox, Orders & Customers, and purchased product consistency.

Covers:
1. One customer with multiple orders.
2. Different SKUs in each order.
3. Different campaigns for each order.
4. Different recommendation sets for each campaign.
5. Selecting the first conversation shows its own order.
6. Selecting the second conversation shows its own order.
7. Customer profile changes correctly when switching conversations.
8. Message order reference matches the triggering campaign.
9. Purchased products are not shown as recommendations.
10. Refreshing the inbox preserves correct associations.
11. No cross-order leakage between different customers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import MessageDeliveryStatus, MessagingProvider, OrderStatus, SimulatorMessageDirection
from app.models.message import MessageLog
from app.models.order import Order, OrderLineItem
from app.models.product import Product, ProductMapping
from app.models.simulator import SimulatorMessage


def _setup_multi_order_fixture(session: Session) -> dict[str, int]:
    """Create two customers:
    Customer 1 has two orders (Order A and Order B) with distinct SKUs.
    Customer 2 has one order (Order C).
    """
    now = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Products and mappings
    # Products for Order A
    p_a1 = Product(product_code="SKU-A1", product_name="Face Wash A1", unit_price=Decimal("199.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)
    p_a2 = Product(product_code="SKU-A2", product_name="Moisturizer A2", unit_price=Decimal("299.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)
    # Recs for Order A
    p_rec_a1 = Product(product_code="REC-A1", product_name="Serum Rec A1", unit_price=Decimal("399.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)
    p_rec_a2 = Product(product_code="REC-A2", product_name="Cream Rec A2", unit_price=Decimal("499.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)

    # Products for Order B
    p_b1 = Product(product_code="SKU-B1", product_name="Hair Oil B1", unit_price=Decimal("150.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)
    p_b2 = Product(product_code="SKU-B2", product_name="Shampoo B2", unit_price=Decimal("250.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)
    # Recs for Order B
    p_rec_b1 = Product(product_code="REC-B1", product_name="Conditioner Rec B1", unit_price=Decimal("350.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)
    p_rec_b2 = Product(product_code="REC-B2", product_name="Scalp Scrub Rec B2", unit_price=Decimal("450.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)

    # Products for Customer 2 (Order C)
    p_c1 = Product(product_code="SKU-C1", product_name="Lip Balm C1", unit_price=Decimal("120.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)
    p_rec_c1 = Product(product_code="REC-C1", product_name="Lip Butter Rec C1", unit_price=Decimal("220.00"), is_rx=False, is_active=True, available_quantity=20, metadata_source="SYNTHETIC_FIXTURE", is_synthetic_metadata=True)

    session.add_all([p_a1, p_a2, p_rec_a1, p_rec_a2, p_b1, p_b2, p_rec_b1, p_rec_b2, p_c1, p_rec_c1])
    session.flush()

    # Mappings
    m_a1 = ProductMapping(source_product_code="SKU-A1", recommended_product_code="REC-A1", recommendation_rank=1)
    m_a2 = ProductMapping(source_product_code="SKU-A2", recommended_product_code="REC-A2", recommendation_rank=1)
    m_b1 = ProductMapping(source_product_code="SKU-B1", recommended_product_code="REC-B1", recommendation_rank=1)
    m_b2 = ProductMapping(source_product_code="SKU-B2", recommended_product_code="REC-B2", recommendation_rank=1)
    m_c1 = ProductMapping(source_product_code="SKU-C1", recommended_product_code="REC-C1", recommendation_rank=1)
    session.add_all([m_a1, m_a2, m_b1, m_b2, m_c1])
    session.flush()

    # Customer 1
    cust1 = Customer(
        external_customer_id="CUST-MULTI-01",
        full_name="Ananya Iyer",
        phone_number="+919876500001",
        whatsapp_opt_in=True,
        opted_out=False,
        consent_source="STOREFRONT_CHECKOUT",
        consent_recorded_at=now,
    )
    # Customer 2
    cust2 = Customer(
        external_customer_id="CUST-MULTI-02",
        full_name="Karan Patel",
        phone_number="+919876500002",
        whatsapp_opt_in=False,
        opted_out=False,
        consent_source=None,
    )
    session.add_all([cust1, cust2])
    session.flush()

    # Customer 1 - Order A
    order_a = Order(
        external_order_id="ORD-MULTI-A01",
        customer_id=cust1.id,
        order_status=OrderStatus.DELIVERED,
        delivered_at=now,
        order_source="STOREFRONT",
        is_demo_order=True,
    )
    order_a.line_items = [
        OrderLineItem(line_index=0, product_code="SKU-A1", product_name="Face Wash A1", quantity=2),
        OrderLineItem(line_index=1, product_code="SKU-A2", product_name="Moisturizer A2", quantity=1),
    ]

    # Customer 1 - Order B
    order_b = Order(
        external_order_id="ORD-MULTI-B02",
        customer_id=cust1.id,
        order_status=OrderStatus.DELIVERED,
        delivered_at=now,
        order_source="STOREFRONT",
        is_demo_order=True,
    )
    order_b.line_items = [
        OrderLineItem(line_index=0, product_code="SKU-B1", product_name="Hair Oil B1", quantity=1),
        OrderLineItem(line_index=1, product_code="SKU-B2", product_name="Shampoo B2", quantity=3),
    ]

    # Customer 2 - Order C
    order_c = Order(
        external_order_id="ORD-MULTI-C03",
        customer_id=cust2.id,
        order_status=OrderStatus.DELIVERED,
        delivered_at=now,
        order_source="STOREFRONT",
        is_demo_order=True,
    )
    order_c.line_items = [
        OrderLineItem(line_index=0, product_code="SKU-C1", product_name="Lip Balm C1", quantity=1),
    ]

    session.add_all([order_a, order_b, order_c])
    session.flush()

    # Campaign for Order A
    camp_a = MessageLog(
        idempotency_key="camp-multi-a01",
        order_id=order_a.id,
        customer_id=cust1.id,
        recipient_phone=cust1.phone_number,
        provider=MessagingProvider.WHATSAPP_SIMULATOR,
        delivery_status=MessageDeliveryStatus.SENT_SIMULATED,
        recommendation_status="SUCCESS",
        recommended_skus=["REC-A1", "REC-A2"],
        decision_trace={
            "selection_provenance": [
                {
                    "recommended_product_code": "REC-A1",
                    "recommended_product_name": "Serum Rec A1",
                    "source_product_code": "SKU-A1",
                    "original_mapping_rank": 1,
                    "selection_sequence": 1,
                },
                {
                    "recommended_product_code": "REC-A2",
                    "recommended_product_name": "Cream Rec A2",
                    "source_product_code": "SKU-A2",
                    "original_mapping_rank": 1,
                    "selection_sequence": 2,
                },
            ]
        },
    )

    # Campaign for Order B
    camp_b = MessageLog(
        idempotency_key="camp-multi-b02",
        order_id=order_b.id,
        customer_id=cust1.id,
        recipient_phone=cust1.phone_number,
        provider=MessagingProvider.WHATSAPP_SIMULATOR,
        delivery_status=MessageDeliveryStatus.SENT_SIMULATED,
        recommendation_status="SUCCESS",
        recommended_skus=["REC-B1", "REC-B2"],
        decision_trace={
            "selection_provenance": [
                {
                    "recommended_product_code": "REC-B1",
                    "recommended_product_name": "Conditioner Rec B1",
                    "source_product_code": "SKU-B1",
                    "original_mapping_rank": 1,
                    "selection_sequence": 1,
                },
                {
                    "recommended_product_code": "REC-B2",
                    "recommended_product_name": "Scalp Scrub Rec B2",
                    "source_product_code": "SKU-B2",
                    "original_mapping_rank": 1,
                    "selection_sequence": 2,
                },
            ]
        },
    )

    # Campaign for Order C (Suppressed because Customer 2 has no consent)
    camp_c = MessageLog(
        idempotency_key="camp-multi-c03",
        order_id=order_c.id,
        customer_id=cust2.id,
        recipient_phone=cust2.phone_number,
        provider=MessagingProvider.WHATSAPP_SIMULATOR,
        delivery_status=MessageDeliveryStatus.SUPPRESSED,
        suppression_reason="NO_MARKETING_CONSENT",
        recommendation_status=None,
        recommended_skus=[],
    )

    session.add_all([camp_a, camp_b, camp_c])
    session.flush()

    # Simulator message for Campaign A
    sim_a = SimulatorMessage(
        simulated_message_id="sim-msg-a01",
        message_log_id=camp_a.id,
        order_id=order_a.id,
        customer_id=cust1.id,
        recipient_phone=cust1.phone_number,
        direction=SimulatorMessageDirection.OUTBOUND,
        message_type="TEMPLATE_RECOMMENDATION",
        body_text=f"Hello! Your Plural order {order_a.external_order_id} has been delivered. Personalized recommendations: REC-A1, REC-A2.",
        recommendations_payload=[
            {"slot_number": 1, "product_code": "REC-A1", "product_name": "Serum Rec A1", "unit_price": 399.0, "source_product_code": "SKU-A1", "source_rank": 1},
            {"slot_number": 2, "product_code": "REC-A2", "product_name": "Cream Rec A2", "unit_price": 499.0, "source_product_code": "SKU-A2", "source_rank": 1},
        ],
        is_simulated=True,
    )

    # Simulator message for Campaign B
    sim_b = SimulatorMessage(
        simulated_message_id="sim-msg-b02",
        message_log_id=camp_b.id,
        order_id=order_b.id,
        customer_id=cust1.id,
        recipient_phone=cust1.phone_number,
        direction=SimulatorMessageDirection.OUTBOUND,
        message_type="TEMPLATE_RECOMMENDATION",
        body_text=f"Hello! Your Plural order {order_b.external_order_id} has been delivered. Personalized recommendations: REC-B1, REC-B2.",
        recommendations_payload=[
            {"slot_number": 1, "product_code": "REC-B1", "product_name": "Conditioner Rec B1", "unit_price": 350.0, "source_product_code": "SKU-B1", "source_rank": 1},
            {"slot_number": 2, "product_code": "REC-B2", "product_name": "Scalp Scrub Rec B2", "unit_price": 450.0, "source_product_code": "SKU-B2", "source_rank": 1},
        ],
        is_simulated=True,
    )

    session.add_all([sim_a, sim_b])
    session.commit()

    return {
        "cust1_id": cust1.id,
        "cust2_id": cust2.id,
        "order_a_id": order_a.id,
        "order_b_id": order_b.id,
        "order_c_id": order_c.id,
        "camp_a_id": camp_a.id,
        "camp_b_id": camp_b.id,
        "camp_c_id": camp_c.id,
    }


def test_one_customer_multiple_orders_different_skus_and_campaigns(db_session: Session, client: TestClient):
    """Test 1, 2, 3, 4: One customer with multiple orders, different SKUs, different campaigns, different rec sets."""
    ids = _setup_multi_order_fixture(db_session)

    # Fetch campaigns for Customer 1
    res = client.get("/api/v1/campaigns")
    assert res.status_code == 200
    campaigns = res.json()

    c_a = next(c for c in campaigns if c["campaign_id"] == ids["camp_a_id"])
    c_b = next(c for c in campaigns if c["campaign_id"] == ids["camp_b_id"])

    # Different orders
    assert c_a["order_id"] == ids["order_a_id"]
    assert c_b["order_id"] == ids["order_b_id"]
    assert c_a["external_order_id"] == "ORD-MULTI-A01"
    assert c_b["external_order_id"] == "ORD-MULTI-B02"

    # Same customer
    assert c_a["customer_id"] == ids["cust1_id"]
    assert c_b["customer_id"] == ids["cust1_id"]
    assert c_a["external_customer_id"] == "CUST-MULTI-01"
    assert c_b["external_customer_id"] == "CUST-MULTI-01"

    # Different recommendations
    assert c_a["recommended_skus"] == ["REC-A1", "REC-A2"]
    assert c_b["recommended_skus"] == ["REC-B1", "REC-B2"]
    assert set(c_a["recommended_skus"]).isdisjoint(set(c_b["recommended_skus"]))


def test_conversation_selection_shows_its_own_order_and_items(db_session: Session, client: TestClient):
    """Test 5 & 6: Selecting conversation A returns Order A and its line items; selecting B returns Order B."""
    ids = _setup_multi_order_fixture(db_session)

    # Select Conversation A
    res_a = client.get(f"/api/v1/campaigns/{ids['camp_a_id']}")
    assert res_a.status_code == 200
    detail_a = res_a.json()

    assert detail_a["external_order_id"] == "ORD-MULTI-A01"
    assert detail_a["customer_id"] == ids["cust1_id"]
    source_skus_a = [item["sku_code"] for item in detail_a["source_items"]]
    assert source_skus_a == ["SKU-A1", "SKU-A2"]
    rec_skus_a = [card["product_code"] for card in detail_a["recommendation_cards"]]
    assert rec_skus_a == ["REC-A1", "REC-A2"]

    # Select Conversation B
    res_b = client.get(f"/api/v1/campaigns/{ids['camp_b_id']}")
    assert res_b.status_code == 200
    detail_b = res_b.json()

    assert detail_b["external_order_id"] == "ORD-MULTI-B02"
    assert detail_b["customer_id"] == ids["cust1_id"]
    source_skus_b = [item["sku_code"] for item in detail_b["source_items"]]
    assert source_skus_b == ["SKU-B1", "SKU-B2"]
    rec_skus_b = [card["product_code"] for card in detail_b["recommendation_cards"]]
    assert rec_skus_b == ["REC-B1", "REC-B2"]


def test_customer_profile_and_order_change_correctly_on_switch(db_session: Session, client: TestClient):
    """Test 7: Detail endpoint returns correct customer consent and order items for each campaign."""
    ids = _setup_multi_order_fixture(db_session)

    # Customer 1 conversation A
    res_a = client.get(f"/api/v1/campaigns/{ids['camp_a_id']}")
    data_a = res_a.json()
    assert data_a["customer_name"] == "Ananya Iyer"
    assert data_a["whatsapp_opt_in"] is True
    assert data_a["opted_out"] is False
    assert data_a["external_order_id"] == "ORD-MULTI-A01"

    # Customer 2 conversation C (Suppressed)
    res_c = client.get(f"/api/v1/campaigns/{ids['camp_c_id']}")
    data_c = res_c.json()
    assert data_c["customer_name"] == "Karan Patel"
    assert data_c["whatsapp_opt_in"] is False
    assert data_c["external_order_id"] == "ORD-MULTI-C03"
    assert data_c["delivery_status"] == "SUPPRESSED"
    assert data_c["suppression_reason"] == "NO_MARKETING_CONSENT"


def test_message_order_reference_matches_campaign_order(db_session: Session, client: TestClient):
    """Test 8: Simulator messages carry the exact external_order_id of their triggering order."""
    ids = _setup_multi_order_fixture(db_session)

    # Messages for Campaign A
    res_msgs_a = client.get(f"/api/v1/simulator/campaigns/{ids['camp_a_id']}/messages")
    assert res_msgs_a.status_code == 200
    msgs_a = res_msgs_a.json()
    assert len(msgs_a) == 1
    assert "ORD-MULTI-A01" in msgs_a[0]["body_text"]
    assert "ORD-MULTI-B02" not in msgs_a[0]["body_text"]
    assert msgs_a[0]["order_id"] == ids["order_a_id"]

    # Messages for Campaign B
    res_msgs_b = client.get(f"/api/v1/simulator/campaigns/{ids['camp_b_id']}/messages")
    assert res_msgs_b.status_code == 200
    msgs_b = res_msgs_b.json()
    assert len(msgs_b) == 1
    assert "ORD-MULTI-B02" in msgs_b[0]["body_text"]
    assert "ORD-MULTI-A01" not in msgs_b[0]["body_text"]
    assert msgs_b[0]["order_id"] == ids["order_b_id"]


def test_purchased_products_are_not_shown_as_recommendations(db_session: Session, client: TestClient):
    """Test 9: In both campaigns, recommendations are strictly disjoint from purchased line items."""
    ids = _setup_multi_order_fixture(db_session)

    detail_a = client.get(f"/api/v1/campaigns/{ids['camp_a_id']}").json()
    purchased_a = {item["sku_code"] for item in detail_a["source_items"]}
    recommended_a = {card["product_code"] for card in detail_a["recommendation_cards"]}
    assert purchased_a.isdisjoint(recommended_a), f"Overlap in Campaign A: {purchased_a & recommended_a}"

    detail_b = client.get(f"/api/v1/campaigns/{ids['camp_b_id']}").json()
    purchased_b = {item["sku_code"] for item in detail_b["source_items"]}
    recommended_b = {card["product_code"] for card in detail_b["recommendation_cards"]}
    assert purchased_b.isdisjoint(recommended_b), f"Overlap in Campaign B: {purchased_b & recommended_b}"


def test_refresh_preserves_correct_associations_and_no_leakage(db_session: Session, client: TestClient):
    """Test 10 & 11: Refreshing /api/v1/campaigns and /api/v1/orders preserves exact 1:1 associations without leakage."""
    ids = _setup_multi_order_fixture(db_session)

    # 1. Campaigns API
    c_list = client.get("/api/v1/campaigns").json()
    camp_map = {c["campaign_id"]: c for c in c_list}

    # Verify Customer 1 campaigns have Customer 1 IDs
    assert camp_map[ids["camp_a_id"]]["customer_id"] == ids["cust1_id"]
    assert camp_map[ids["camp_b_id"]]["customer_id"] == ids["cust1_id"]
    # Verify Customer 2 campaign has Customer 2 ID
    assert camp_map[ids["camp_c_id"]]["customer_id"] == ids["cust2_id"]

    # 2. Orders API
    o_list = client.get("/api/v1/orders").json()
    order_map = {o["id"]: o for o in o_list}

    # Order A
    ord_a = order_map[ids["order_a_id"]]
    assert ord_a["customer_id"] == ids["cust1_id"]
    assert ord_a["campaign_id"] == ids["camp_a_id"]
    assert [i["product_code"] for i in ord_a["items"]] == ["SKU-A1", "SKU-A2"]
    assert ord_a["recommended_skus"] == ["REC-A1", "REC-A2"]

    # Order B
    ord_b = order_map[ids["order_b_id"]]
    assert ord_b["customer_id"] == ids["cust1_id"]
    assert ord_b["campaign_id"] == ids["camp_b_id"]
    assert [i["product_code"] for i in ord_b["items"]] == ["SKU-B1", "SKU-B2"]
    assert ord_b["recommended_skus"] == ["REC-B1", "REC-B2"]

    # Order C
    ord_c = order_map[ids["order_c_id"]]
    assert ord_c["customer_id"] == ids["cust2_id"]
    assert ord_c["campaign_id"] == ids["camp_c_id"]
    assert [i["product_code"] for i in ord_c["items"]] == ["SKU-C1"]
    assert ord_c["recommended_skus"] == []  # Suppressed


def test_orders_api_separates_purchased_items_and_recommendations(db_session: Session, client: TestClient):
    """Test: /api/v1/orders returns both items and recommended_skus cleanly separated."""
    ids = _setup_multi_order_fixture(db_session)

    orders = client.get("/api/v1/orders").json()
    ord_a = next(o for o in orders if o["id"] == ids["order_a_id"])

    # Purchased items
    assert len(ord_a["items"]) == 2
    assert ord_a["items"][0]["product_code"] == "SKU-A1"
    assert ord_a["items"][0]["product_name"] == "Face Wash A1"
    assert ord_a["items"][1]["product_code"] == "SKU-A2"
    assert ord_a["items"][1]["product_name"] == "Moisturizer A2"

    # Recommended SKUs & Products
    assert ord_a["recommended_skus"] == ["REC-A1", "REC-A2"]
    assert len(ord_a["recommended_products"]) == 2
    assert ord_a["recommended_products"][0]["product_code"] == "REC-A1"
    assert ord_a["recommended_products"][0]["product_name"] == "Serum Rec A1"
    assert ord_a["recommended_products"][1]["product_code"] == "REC-A2"
    assert ord_a["recommended_products"][1]["product_name"] == "Cream Rec A2"

    # Ensure no overlap
    purchased_set = {i["product_code"] for i in ord_a["items"]}
    rec_set = set(ord_a["recommended_skus"])
    assert purchased_set.isdisjoint(rec_set)
