# Stage 8: Ecommerce Storefront, Automated Demo Delivery & Real WhatsApp Cloud API

## Overview
Stage 8 connects the deterministic post-delivery recommendation engine and WhatsApp dispatch coordinator with a full customer-facing ecommerce storefront, automated demo delivery progression, and live Meta WhatsApp Cloud API integration.

---

## 1. Storefront Experience (`/store`)
- **Route**: `GET /store` (and `GET /store/order/{order_id}` for live tracking).
- **Catalogue Grid**: Fetches all 70 seeded products from `/api/v1/products`, rendering category badges, Rx / OTC status, prices, and instant search/filtering.
- **Cart & Checkout Drawer**:
  - Live quantity adjustment (`+` / `-`), removal, and subtotal calculation.
  - Checkout form:
    - Customer Name (mandatory)
    - Strict E.164 Mobile Number with `+` prefix validation (`^\+[1-9]\d{7,14}$`)
    - Delivery Address (mandatory)
    - **Explicit WhatsApp Marketing Consent**:
      - Unchecked by default.
      - Discloses purpose and opt-out rights.
      - Sets `consent_source = "STOREFRONT_CHECKOUT"` and `consent_recorded_at = now()`.

---

## 2. Automated Demo Delivery Progression
- **Configuration**:
  - `DEMO_DELIVERY_ENABLED`: Boolean flag gating background demo progression (defaults to `False` in production and staging).
  - `DEMO_DELIVERY_DELAY_SECONDS`: Delay between order placement and `DELIVERED` state (default `12.0` seconds).
- **Milestone Stepper**:
  1. `PLACED` (0% elapsed)
  2. `CONFIRMED` (~25% elapsed)
  3. `PACKED` (~50% elapsed)
  4. `OUT_FOR_DELIVERY` (~75% elapsed)
  5. `DELIVERED` (100% elapsed / time expired)
- **Engine Trigger**:
  - Reaching `DELIVERED` triggers `CampaignOrchestrationService.process_order_delivered_event`.
  - The deterministic Stage 4 engine generates up to 6 eligible cross-sell recommendations.
  - If customer provided consent, campaign is prepared as `READY_FOR_DISPATCH`.
  - `DispatchCoordinator.dispatch_campaign` dispatches the recommendation template via the configured provider (`META_CLOUD_API` or `WHATSAPP_SIMULATOR`).

---

## 3. Inbound WhatsApp Messages & Opt-Out Workflow
- **Webhook Endpoint**: `POST /api/v1/webhooks/whatsapp`.
- **Deduplication**: Inbound messages in `entry[].changes[].value.messages[]` are claimed idempotently via `webhook_events.event_id = "wa_inbound:{wamid}"`.
- **Persistence**: Logged in `whatsapp_messages` with `direction = "INBOUND"`, linked to customer by phone.
- **Deterministic Keyword Handler**:
  - `STOP` / `UNSUBSCRIBE`:
    - `customer.opted_out = True`
    - `customer.whatsapp_opt_in = False`
    - `customer.opt_out_recorded_at = now()`
    - Suppresses all future marketing campaigns.
    - Sends automated confirmation: `"You have been successfully unsubscribed from Plural WhatsApp updates. Reply START to resubscribe anytime."`
  - `START` / `RESUBSCRIBE`:
    - `customer.opted_out = False`
    - `customer.whatsapp_opt_in = True`
    - `customer.consent_source = "WHATSAPP_INBOUND_START"`
    - Sends automated confirmation: `"You have successfully subscribed to Plural WhatsApp updates. Reply STOP anytime to unsubscribe."`
  - `HELP` / `SUPPORT`:
    - Sends customer support assistance details.

---

## 4. API Endpoints
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/store/orders` | Checkout cart, persist customer & order, schedule demo delivery |
| `GET` | `/api/v1/store/orders/{order_id}/tracking` | Real-time milestone stepper, countdown, and post-delivery recommendations |
| `GET` | `/api/v1/webhooks/whatsapp` | Meta webhook subscription verification handshake |
| `POST` | `/api/v1/webhooks/whatsapp` | Meta webhook receiver for delivery status updates and customer replies |
| `GET` | `/store` | Customer storefront UI |
| `GET` | `/store/order/{order_id}` | Customer order tracking UI |
| `GET` | `/simulator` | Admin Dashboard, Campaigns, Decision Traces, WhatsApp Inbox |

---

## 5. Verification & Testing
- Total automated tests: **161 passed** across Stages 1 through 8.
- Host test command: `pytest`
- Docker test command: `docker compose exec backend python -m pytest`
