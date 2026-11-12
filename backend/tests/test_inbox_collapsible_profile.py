"""Test WhatsApp Inbox Collapsible Customer Profile and Native 100% Zoom Layout.

Verifies:
1. DOM structure of the 3-pane WhatsApp Inbox.
2. Collapsible customer profile panel controls (header button and drawer close button).
3. CSS Grid rules for expanded vs collapsed profile state.
4. Retention of all 6 customer profile sections upon toggle.
5. Strict separation between purchased items and recommendations.
6. Responsive overlay drawer support for smaller viewports.
"""

from fastapi.testclient import TestClient


def test_inbox_dom_elements_for_collapsible_profile(client: TestClient) -> None:
    """Requirement 1 & 3: Container has required IDs and backdrop for collapsible drawer."""
    resp = client.get("/simulator")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="inbox-container"' in html
    assert 'id="inbox-left"' in html
    assert 'id="inbox-center"' in html
    assert 'id="inbox-right-drawer"' in html
    assert 'id="inbox-drawer-backdrop"' in html


def test_inbox_css_collapsible_and_grid_rules(client: TestClient) -> None:
    """Requirement 2 & 3: CSS defines flexible grid columns and profile-closed state."""
    resp = client.get("/static/css/styles.css")
    assert resp.status_code == 200
    css = resp.text

    # Default 3-column grid with minmax(0, 1fr) flexible chat
    assert ".inbox-container" in css
    assert "grid-template-columns: 290px minmax(0, 1fr) 350px;" in css

    # Collapsed state expands chat naturally
    assert ".inbox-container.profile-closed" in css
    assert "grid-template-columns: 290px minmax(0, 1fr);" in css
    assert ".inbox-container.profile-closed .inbox-right" in css
    assert "display: none !important;" in css

    # Toggle button styles
    assert ".btn-toggle-profile" in css
    assert ".btn-close-drawer" in css
    assert ".drawer-header-row" in css

    # Responsive tablet/laptop overlay drawer
    assert "@media (max-width: 1199px)" in css
    assert "transform: translateX(100%);" in css
    assert ".inbox-container:not(.profile-closed) .inbox-right" in css
    assert "transform: translateX(0);" in css


def test_inbox_js_toggle_profile_functionality(client: TestClient) -> None:
    """Requirement 1 & 8: JavaScript exposes toggleProfilePanel and state management."""
    resp = client.get("/static/js/app.js")
    assert resp.status_code == 200
    js = resp.text

    assert "toggleProfilePanel" in js
    assert "btn-toggle-profile" in js
    assert "btn-close-drawer" in js
    assert "Hide customer profile" in js
    assert "Show customer profile" in js
    assert "profile-closed" in js
    assert "inbox_profile_open" in js


def test_inbox_right_drawer_all_six_sections_preserved(client: TestClient) -> None:
    """Requirement 5: All 6 required intelligence sections remain in the customer drawer."""
    resp = client.get("/static/js/app.js")
    assert resp.status_code == 200
    js = resp.text

    # 1. Customer Profile
    assert "Customer Profile" in js
    # 2. Consent State
    assert "Consent State" in js
    # 3. Delivered Order Items (Purchased)
    assert "Delivered Order Items" in js
    assert "badge-purchased" in js
    # 4. Cross-Sell Recommendations
    assert "Cross-Sell Recommendations" in js
    assert "badge-rec" in js
    # 5. Campaign & Dispatch
    assert "Campaign &amp; Dispatch" in js
    # 6. Decision Trace
    assert "Decision Trace" in js
    assert "openCampaignDrawer" in js
