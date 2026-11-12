"""Test Navigation & Information Architecture Refinement.

Verifies:
1. Customer-facing storefront as default homepage (/) and (/store).
2. Storefront header navigation elements: Shop, Catalogue, WhatsApp Inbox (/simulator?view=whatsapp-inbox), Cart, Admin (/simulator).
3. Admin dashboard (/simulator) with exactly four sidebar navigation items:
   - Overview
   - Campaigns
   - WhatsApp Inbox
   - Orders & Customers
4. Removal of Product Catalogue, Demo Storefront, and Settings from the admin sidebar.
5. Admin header contains Storefront shortcut, Provider status badge, and Refresh.
6. Direct URLs and view routing parameters.
7. Integrity of product catalogue API and storefront order placement.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import OrderStatus
from app.models.product import Product


def test_homepage_and_storefront_routes(client: TestClient) -> None:
    """Requirement 1 & 6: Root / and /store serve the customer-facing storefront."""
    # 1. Canonical homepage /
    resp_root = client.get("/", follow_redirects=False)
    assert resp_root.status_code == 200
    assert "text/html" in resp_root.headers.get("content-type", "")
    assert "Plural Health" in resp_root.text
    assert "Everyday wellness, made simpler." in resp_root.text

    # 2. Storefront route /store
    resp_store = client.get("/store", follow_redirects=False)
    assert resp_store.status_code == 200
    assert "text/html" in resp_store.headers.get("content-type", "")
    assert "Plural Health" in resp_store.text


def test_storefront_header_navigation_and_labels(client: TestClient) -> None:
    """Requirement 1 & 2: Storefront header contains correct navigation items and target routes."""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Branding
    assert "Plural Health" in html

    # Shop link
    assert 'href="/" class="nav-link-item active">Shop</a>' in html or 'href="/" class="nav-link-item' in html

    # Catalogue anchor
    assert 'href="#catalogue"' in html
    assert "Catalogue" in html

    # WhatsApp Inbox shortcut to /simulator?view=whatsapp-inbox
    assert 'href="/simulator?view=whatsapp-inbox"' in html
    assert "WhatsApp Inbox" in html

    # Cart button
    assert 'id="btn-open-cart"' in html
    assert "Cart" in html

    # Admin button to /simulator
    assert 'href="/simulator" class="admin-switch-btn"' in html
    assert "Admin" in html


def test_admin_dashboard_sidebar_four_items_only(client: TestClient) -> None:
    """Requirement 1, 3 & 10: Admin dashboard sidebar contains only the 4 requested items."""
    resp = client.get("/simulator")
    assert resp.status_code == 200
    html = resp.text

    # Verify Sidebar container
    assert '<nav class="sidebar-nav">' in html

    # 1. Overview
    assert 'data-view="overview"' in html
    assert "Overview" in html

    # 2. Campaigns
    assert 'data-view="campaigns"' in html
    assert "Campaigns" in html

    # 3. WhatsApp Inbox
    assert 'data-view="inbox"' in html
    assert "WhatsApp Inbox" in html

    # 4. Orders & Customers
    assert 'data-view="orders-customers"' in html
    assert "Orders & Customers" in html

    # Verify OBSOLETE sidebar items are strictly removed from the sidebar-nav
    # Extract sidebar nav content
    nav_start = html.find('<nav class="sidebar-nav">')
    nav_end = html.find('</nav>', nav_start)
    sidebar_html = html[nav_start:nav_end]

    assert 'data-view="catalogue"' not in sidebar_html, "Product Catalogue must not be in sidebar nav"
    assert 'href="/store"' not in sidebar_html, "Demo Storefront must not be in sidebar nav"
    assert 'data-view="settings"' not in sidebar_html, "Settings must not be in sidebar nav"
    assert 'Product Catalogue' not in sidebar_html, "Product Catalogue label must not be in sidebar nav"


def test_admin_dashboard_header_controls(client: TestClient) -> None:
    """Requirement 5: Admin dashboard header contains clean controls including Storefront shortcut."""
    resp = client.get("/simulator")
    assert resp.status_code == 200
    html = resp.text

    # Topbar header
    assert 'id="topbar-title"' in html
    assert 'id="provider-indicator-pill"' in html
    assert 'id="btn-refresh"' in html

    # Shortcut back to Store
    assert 'href="/"' in html or 'href="/store"' in html
    assert "Store" in html
    assert 'id="btn-topbar-store"' in html


def test_provider_status_api_and_mode_clarity(client: TestClient) -> None:
    """Requirement 2.C: Active messaging provider API returns transparent provider configuration."""
    resp = client.get("/api/v1/campaigns/provider-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "configured_provider" in data
    assert "active_provider" in data
    assert data["active_provider"] in ("WHATSAPP_SIMULATOR", "META_CLOUD_API")


def test_storefront_catalogue_api_available(client: TestClient, db_session: Session) -> None:
    """Requirement 1 & 7: Underlying product catalogue remains fully functional."""
    # Ensure test product exists in session
    p = db_session.query(Product).filter_by(product_code="NAV-TEST-01").first()
    if p is None:
        p = Product(
            product_code="NAV-TEST-01",
            product_name="Digital Thermometer",
            unit_price=299.00,
            is_rx=False,
            is_active=True,
            available_quantity=20,
        )
        db_session.add(p)
        db_session.commit()

    resp = client.get("/api/v1/products")
    assert resp.status_code == 200
    products = resp.json()
    assert isinstance(products, list)
    assert any(prod["product_code"] == "NAV-TEST-01" for prod in products)


def test_storefront_order_tracking_route(client: TestClient) -> None:
    """Requirement 4 & 6: Order tracking route /store/order/{order_id} continues to serve storefront."""
    resp = client.get("/store/order/ORD-TEST-001")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Plural Health" in resp.text
