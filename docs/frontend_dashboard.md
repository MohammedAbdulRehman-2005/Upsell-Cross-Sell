# Stage 7 — Plural Commerce Automation Frontend Dashboard

## Overview
Stage 7 builds a modern, polished, responsive business dashboard for the **Plural WhatsApp Upsell & Cross-Sell Platform** inspired by the clean SaaS admin designs in `sample/`.

The dashboard is served directly by FastAPI at `http://localhost:8000/` and `http://localhost:8000/simulator`. It uses a modular vanilla HTML5, CSS3, and ES6 JavaScript single-page application (SPA) architecture with zero additional npm/node build dependencies, guaranteeing instant, reliable execution across both local host and Docker environments.

---

## 1. Application Architecture

```
backend/app/static/
├── css/
│   └── styles.css          # SaaS design system (navy sidebar, crisp white cards, slate accents)
├── js/
│   ├── api.js              # Centralized REST API client with timeout, error handling, typing helpers
│   └── app.js              # View router, state store, interactive simulator logic, modal controllers
└── simulator/
    └── index.html          # Semantic HTML5 shell with sidebar, topbar, view panes, modals, toasts
```

### Design Language & Branding
- **Product Name**: Plural Commerce Automation
- **Product Description**: WhatsApp Upsell & Cross-sell Platform
- **Color Palette**:
  - Restrained dark navy sidebar (`#0f172a`) with subtle dividers (`#1e293b`)
  - Clean neutral slate app canvas (`#f8fafc`) with crisp white card surfaces (`#ffffff`)
  - Status Accents:
    - `READY_FOR_DISPATCH`: Amber badge (`#fef3c7` / `#92400e`)
    - `SENT_SIMULATED`: Cyan/blue badge (`#e0f2fe` / `#0369a1`)
    - `PROVIDER_ACCEPTED`: Purple badge (`#ede9fe` / `#5b21b6`)
    - `DELIVERED`: Emerald green badge (`#dcfce7` / `#166534`)
    - `READ`: Teal badge (`#ccfbf1` / `#115e59`)
    - `SUPPRESSED`: Neutral slate badge (`#f1f5f9` / `#475569`)
    - `FAILED_*` / `DELIVERY_UNKNOWN`: Crimson badge (`#fee2e2` / `#991b1b`)

---

## 2. Six Core Dashboard Views

### View 1: Overview Dashboard
- **KPI Metrics**: Total Campaigns, Ready for Dispatch, Simulator Sent, Meta Provider Accepted, Confirmed Delivered, and Suppressed.
- **Provider Status Banner**: Real-time indication of whether the local simulator or live Meta WhatsApp Cloud API is active, and whether credentials/templates are configured.
- **Recent Campaigns Table**: Top 5 most recent campaigns with instant click-through to campaign details.

### View 2: Campaign Management
- **Search & Filters**: Real-time search across order IDs, customer IDs, recipient phone numbers, and recommended SKUs; status dropdown filter.
- **Campaigns Data Table**: Displays Campaign ID, Order Reference, Customer, Recipient Phone, Delivery Status badge, Recommended SKUs, Provider Information, and Timestamps.
- **Decision Trace Drawer**: Click "Details" on any campaign to inspect the step-by-step deterministic decision trace, source line items, candidate product evaluations, and metadata provenance.
- **Dispatch Action Guard**: Dispatches are enabled only when the backend reports `READY_FOR_DISPATCH` or `FAILED_RETRYABLE`, with an explicit confirmation dialog before sending.

### View 3: WhatsApp Commerce Inbox (Core Feature)
- **Three-Column Experience**:
  - **Left**: Chronological conversation list with customer avatar, external order ID, customer name, recipient phone, and latest delivery badge.
  - **Center**: WhatsApp-styled conversation stream with speech bubbles, explicit `[SIMULATED MESSAGE]` provenance labels, rendered product recommendation cards with unit prices and action buttons (`View Details`, `Interested`, `Pass`), and bottom customer reply buttons (`View All Recommendations`, `Interested in Order`, `Not Interested`).
  - **Right**: Customer profile drawer showing customer ID, phone, active consent state, delivered order items, delivery timestamps, and a quick-link to the full decision trace.
- **Real Backend Persistence**: Every simulated customer interaction posts directly to `POST /api/v1/simulator/campaigns/{id}/interact`, persisting customer inbound messages and simulated assistant outbound replies to the PostgreSQL `simulator_messages` table.

### View 4: Orders & Customers
- **Tab 1 — Orders**: Persisted customer orders with external order ID, customer reference, order status, delivery timestamp, line items preview, and associated campaign delivery state.
- **Tab 2 — Customers & Consent**: Customer profiles with privacy-safe masked phone numbers (`+91******3210`), WhatsApp opt-in state, opt-out status, and consent capture provenance (`consent_source` and `consent_recorded_at`).

### View 5: Product Catalogue & Mappings
- **Authoritative 70-SKU Table**: Product code, product name, unit price, Rx prescription status (`OTC ELIGIBLE` vs `Rx PRESCRIPTION`), active status, available stock quantity, metadata provenance (`WORKBOOK_ONLY`, `SYNTHETIC_FIXTURE`, `VERIFIED_CATALOGUE`), and mapped recommendation count.
- **Search**: Instant case-insensitive filtering by SKU code or product title.
- **Mapping Modal**: Click "View Mappings" on any product to inspect all 6 original ranked co-purchase recommendations with target prices, Rx status, and target catalogue provenance.

### View 6: Settings & Integration Status
- **Active Messaging Gateway**: Current provider mode (`WHATSAPP_SIMULATOR` vs `META_CLOUD_API`).
- **Meta Graph API Health**: Verification of API version (`v21.0`), template name (`plural_post_delivery_upsell_v1`), language (`en_US`), and HTTP timeout (10s).
- **Webhook Handshake Configuration**: Verification status of `hub.verify_token` and `X-Hub-Signature-256` app secret.
- **Backend & Database Health**: Live probe inspection of `GET /health/live` and `GET /health/ready` (PostgreSQL connection check).
- **Security Invariant**: Access tokens and secrets are never displayed or stored in frontend browser storage.

---

## 3. Backend REST APIs

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/simulator` & `/` | Serves the single-page frontend application |
| `GET` | `/static/*` | Serves static CSS and JS assets |
| `GET` | `/api/v1/analytics/overview` | Returns aggregate dashboard KPIs and recent campaigns |
| `GET` | `/api/v1/campaigns` | Lists all post-delivery upsell campaigns |
| `GET` | `/api/v1/campaigns/{id}` | Returns campaign detail, recommendation cards, and decision trace |
| `POST` | `/api/v1/campaigns/{id}/dispatch` | Triggers outbound dispatch via the active provider |
| `GET` | `/api/v1/campaigns/provider-status` | Returns provider mode and configuration status |
| `GET` | `/api/v1/simulator/campaigns/{id}/messages` | Returns chronological simulated conversation messages |
| `POST` | `/api/v1/simulator/campaigns/{id}/interact` | Records simulated customer action and generates assistant reply |
| `POST` | `/api/v1/simulator/demo-seed` | Idempotently seeds 3 demo walkthrough campaigns |
| `GET` | `/api/v1/orders` | Lists persisted orders with line items and campaign status |
| `GET` | `/api/v1/customers` | Lists customers with masked phone numbers and consent provenance |
| `GET` | `/api/v1/products` | Lists 70 catalogue products with search filter |
| `GET` | `/api/v1/products/{code}` | Returns product detail with all 6 ranked product mappings |
| `GET` | `/health/live`, `/health/ready` | Liveness and readiness health probes |
