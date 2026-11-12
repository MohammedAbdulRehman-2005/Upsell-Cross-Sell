"""Tests for Stage 7 dashboard overview, orders, customers, and product catalogue APIs."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import MessageDeliveryStatus, MessagingProvider, OrderStatus
from app.models.message import MessageLog
from app.models.order import Order, OrderLineItem
from app.models.product import Product, ProductMapping


def _setup_stage7_sample_data(session: Session) -> None:
    # 1. Product & Mappings
    p1 = Product(
        product_code="SRC-MED-01",
        product_name="Ayurvedic Digestive Tonic",
        unit_price=Decimal("150.00"),
        is_rx=False,
        is_active=True,
        available_quantity=30,
        metadata_source="SYNTHETIC_FIXTURE",
        is_synthetic_metadata=True,
    )
    p2 = Product(
        product_code="TGT-MED-01",
        product_name="Probiotic Capsules",
        unit_price=Decimal("350.00"),
        is_rx=False,
        is_active=True,
        available_quantity=20,
        metadata_source="SYNTHETIC_FIXTURE",
        is_synthetic_metadata=True,
    )
    session.add_all([p1, p2])
    session.flush()

    m1 = ProductMapping(
        source_product_code="SRC-MED-01",
        recommended_product_code="TGT-MED-01",
        recommendation_rank=1,
    )
    session.add(m1)

    # 2. Customer
    cust = Customer(
        external_customer_id="CUST-STG7-01",
        full_name="Rajesh Sharma",
        phone_number="+919876543210",
        whatsapp_opt_in=True,
        opted_out=False,
        consent_source="CHECKOUT_OPT_IN",
        consent_recorded_at=datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc),
    )
    session.add(cust)
    session.flush()

    # 3. Order
    order = Order(
        external_order_id="ORD-STG7-100",
        customer_id=cust.id,
        order_status=OrderStatus.DELIVERED,
        delivered_at=datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc),
    )
    order.line_items = [
        OrderLineItem(line_index=0, product_code="SRC-MED-01", product_name="Ayurvedic Digestive Tonic", quantity=2)
    ]
    session.add(order)
    session.flush()

    # 4. Campaign
    camp = MessageLog(
        idempotency_key="campaign:post_delivery:ORD-STG7-100",
        order_id=order.id,
        customer_id=cust.id,
        campaign_type="POST_DELIVERY_UPSELL",
        recipient_phone="+919876543210",
        provider=MessagingProvider.WHATSAPP_SIMULATOR,
        delivery_status=MessageDeliveryStatus.READY_FOR_DISPATCH,
        recommended_skus=["TGT-MED-01"],
        recommendation_status="SUCCESS",
    )
    session.add(camp)
    session.commit()


def test_analytics_overview_api(client: TestClient, db_session: Session) -> None:
    _setup_stage7_sample_data(db_session)
    resp = client.get("/api/v1/analytics/overview")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_campaigns"] == 1
    assert data["ready_for_dispatch"] == 1
    assert data["total_orders"] == 1
    assert data["delivered_orders"] == 1
    assert data["total_customers"] == 1
    assert data["opted_in_customers"] == 1
    assert data["total_products"] == 2
    assert data["active_provider"] == "WHATSAPP_SIMULATOR"
    assert data["is_provider_configured"] is True
    assert len(data["recent_campaigns"]) == 1
    assert data["recent_campaigns"][0]["external_order_id"] == "ORD-STG7-100"


def test_customers_list_api(client: TestClient, db_session: Session) -> None:
    _setup_stage7_sample_data(db_session)
    resp = client.get("/api/v1/customers")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    c = items[0]
    assert c["external_customer_id"] == "CUST-STG7-01"
    assert c["full_name"] == "Rajesh Sharma"
    # Verify phone number is masked for privacy
    assert c["masked_phone"] != "+919876543210"
    assert c["masked_phone"].startswith("+91") and c["masked_phone"].endswith("3210") and "*" in c["masked_phone"]
    assert c["whatsapp_opt_in"] is True
    assert c["opted_out"] is False
    assert c["consent_source"] == "CHECKOUT_OPT_IN"
    assert c["order_count"] == 1
    assert c["campaign_count"] == 1


def test_orders_list_api(client: TestClient, db_session: Session) -> None:
    _setup_stage7_sample_data(db_session)
    resp = client.get("/api/v1/orders")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    o = items[0]
    assert o["external_order_id"] == "ORD-STG7-100"
    assert o["customer_external_id"] == "CUST-STG7-01"
    assert o["order_status"] == "DELIVERED"
    assert o["line_item_count"] == 1
    assert o["campaign_status"] == "READY_FOR_DISPATCH"
    assert o["items"][0]["product_code"] == "SRC-MED-01"
    assert o["items"][0]["product_name"] == "Ayurvedic Digestive Tonic"
    assert o["items"][0]["quantity"] == 2
    assert "recommended_products" in o
    assert len(o["recommended_products"]) == 1
    assert o["recommended_products"][0]["product_code"] == "TGT-MED-01"
    assert o["recommended_products"][0]["product_name"] == "Probiotic Capsules"


def test_orders_list_api_resolves_product_name_from_catalogue_when_missing(
    client: TestClient, db_session: Session
) -> None:
    """Verify data integrity: line item product name is resolved from products table when order_line_items.product_name is None."""
    _setup_stage7_sample_data(db_session)
    # Set product_name on order_line_item to None
    line_item = db_session.query(OrderLineItem).first()
    assert line_item is not None
    line_item.product_name = None
    db_session.commit()

    resp = client.get("/api/v1/orders")
    assert resp.status_code == 200
    orders = resp.json()
    assert len(orders) == 1
    assert orders[0]["items"][0]["product_name"] == "Ayurvedic Digestive Tonic"
    assert orders[0]["recommended_products"][0]["product_name"] == "Probiotic Capsules"


def test_products_list_and_search_api(client: TestClient, db_session: Session) -> None:
    _setup_stage7_sample_data(db_session)
    # 1. List all
    resp = client.get("/api/v1/products")
    assert resp.status_code == 200, resp.text
    products = resp.json()
    assert len(products) == 2
    src = next(p for p in products if p["product_code"] == "SRC-MED-01")
    assert src["product_name"] == "Ayurvedic Digestive Tonic"
    assert src["unit_price"] == 150.00
    assert src["is_rx"] is False
    assert src["metadata_source"] == "SYNTHETIC_FIXTURE"
    assert src["mapped_recommendations_count"] == 1

    # 2. Search query filter
    search_resp = client.get("/api/v1/products?q=Digestive")
    assert search_resp.status_code == 200
    search_results = search_resp.json()
    assert len(search_results) == 1
    assert search_results[0]["product_code"] == "SRC-MED-01"


def test_product_detail_api(client: TestClient, db_session: Session) -> None:
    _setup_stage7_sample_data(db_session)
    # 1. Existing product
    resp = client.get("/api/v1/products/SRC-MED-01")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["product_code"] == "SRC-MED-01"
    assert data["mapped_recommendations_count"] == 1
    assert len(data["mappings"]) == 1
    m = data["mappings"][0]
    assert m["rank"] == 1
    assert m["recommended_product_code"] == "TGT-MED-01"
    assert m["recommended_product_name"] == "Probiotic Capsules"
    assert m["unit_price"] == 350.00

    # 2. Non-existent product -> 404
    missing_resp = client.get("/api/v1/products/NON-EXISTENT")
    assert missing_resp.status_code == 404


def test_static_dashboard_pages_and_assets(client: TestClient) -> None:
    # 1. Simulator / Dashboard page serves HTML
    resp_page = client.get("/simulator")
    assert resp_page.status_code == 200
    assert "text/html" in resp_page.headers.get("content-type", "")
    assert "Plural Commerce" in resp_page.text
    assert "Overview" in resp_page.text
    assert "WhatsApp Inbox" in resp_page.text

    # 2. Root serves storefront homepage directly
    resp_root = client.get("/", follow_redirects=False)
    assert resp_root.status_code == 200
    assert "text/html" in resp_root.headers.get("content-type", "")
    assert "Plural Health" in resp_root.text

    # Storefront route also serves storefront homepage
    resp_store = client.get("/store", follow_redirects=False)
    assert resp_store.status_code == 200
    assert "text/html" in resp_store.headers.get("content-type", "")
    assert "Plural Health" in resp_store.text

    # 3. CSS stylesheet serves correctly
    resp_css = client.get("/static/css/styles.css")
    assert resp_css.status_code == 200
    assert "text/css" in resp_css.headers.get("content-type", "")
    assert "--bg-sidebar" in resp_css.text

    # 4. JS scripts serve correctly
    resp_api_js = client.get("/static/js/api.js")
    assert resp_api_js.status_code == 200
    assert "ApiClient" in resp_api_js.text

    resp_app_js = client.get("/static/js/app.js")
    assert resp_app_js.status_code == 200
    assert "switchView" in resp_app_js.text
