# Plural WhatsApp Upsell / Cross-sell Prototype

Stage 1 deliverables: normalized mapping dataset, source validation report, finalized requirements/assumptions, recommendation algorithm specification, and test matrix.

## Current status

- Source workbook structurally validated: 70 source products, 420 ranked mapping entries (10 isolated mapping clusters of 7 products each; the workbook establishes recommendation graph structure, not actual product category membership).
- Normalized JSON and flat CSV generated and verified against SHA-256 checksums.
- Requirements, architectural decisions, recommendation algorithm, and 24 acceptance test cases finalized.
- **Stage 2 backend foundation completed**: FastAPI application, Pydantic v2 settings & schemas, SQLAlchemy 2.x models & repositories, Alembic migrations (`20261111_0001`, `20261111_0002`, `20261111_0003`), Docker Compose stack (PostgreSQL 16 + FastAPI), and `/health/live` & `/health/ready` endpoints.
- **Stage 3 product catalogue seeding & mapping data integrity completed**:
  - Cross-verified authoritative workbook (`data/raw/product_mapping_source.xlsx`), normalized JSON, flat CSV, and validation report.
  - Seeded all **70 products** and **420 ranked mappings** (`ranks 1..6`) idempotently into PostgreSQL.
  - Explicitly separated workbook-only unknown metadata (`metadata_source = WORKBOOK_ONLY`, `is_rx/is_active/available_quantity = NULL`), synthetic demo fixtures (`SYNTHETIC_FIXTURE`, `is_synthetic_metadata = true`), and verified catalogue records (`VERIFIED_CATALOGUE`).
- **Stage 4, 4.1 & 4.2 deterministic recommendation engine, trace audit & PostgreSQL provenance verification completed**:
  - Pure Python recommendation engine (`backend/app/services/recommendation/`) decoupled from FastAPI, SQLAlchemy sessions, HTTP clients, and LLMs, paired with a batch-loading `DatabaseRecommendationService`.
  - Implements all 10 business rules: max-6 cap without artificial padding, purchased exclusion (`ALREADY_PURCHASED`), repeated order-line normalization (`quantity_sum`, `first_line_index`), fail-closed eligibility (`CATALOGUE_METADATA_MISSING`, `RX_PRODUCT_EXCLUDED`, `PRODUCT_INACTIVE`, `PRODUCT_UNAVAILABLE`), rank preservation (`1..6`), conditional source prioritization (`M <= 6` vs `M > 6`), equal initial allocation (`INITIAL_ALLOCATION`), cross-source deduplication (`DUPLICATE_RECOMMENDATION`), round-robin `BACKFILL`, and complete JSON-serializable decision traces (`DecisionTrace`).
  - Explicitly distinguishes pre-prioritization **eligibility evaluation** (`eligibility_evaluations_count`, `eligible_candidate_count`, `rejected_candidate_count`, `eligibility_rejection_reasons`) from **cursor-based allocation scanning** (`allocation_candidates_examined`, `initial_allocation_selected_count`, `backfill_selected_count`, `duplicate_candidate_count`, `unvisited_candidate_count`, `examined_during_allocation`, `allocation_decision`).
- **Stage 5 order-delivery event processing & campaign orchestration completed**:
  - Exposed `POST /api/v1/webhooks/order-delivered` with configurable webhook authentication (`dev_insecure_open` for local/test only—failing closed in `staging`/`production`—and `shared_token_header` verifying `X-Webhook-Token` via `hmac.compare_digest`).
  - Enforced authoritative persisted order-state checks: only orders whose persisted PostgreSQL status is `DELIVERED` trigger campaign processing (`PENDING`, `PROCESSING`, `SHIPPED`, and `CANCELLED` are ignored even if the webhook payload claims `DELIVERED`).
  - Implemented multi-layer idempotency and concurrency control (`webhook_events.event_id` uniqueness + `SELECT ... FOR UPDATE` row locking on `Order` + `uq_message_logs_order_campaign` uniqueness on `(order_id, campaign_type)`), plus safe retry recovery (`FAILED_RETRYABLE` -> `READY_FOR_DISPATCH`).
  - Enforced fail-closed customer consent, opt-out, and phone destination validation (`CUSTOMER_PHONE_MISSING`, `INVALID_WHATSAPP_DESTINATION`, `CONSENT_STATUS_UNKNOWN`, `CUSTOMER_OPTED_OUT`, `WHATSAPP_MARKETING_CONSENT_MISSING`).
  - Integrated Stage 4 `DatabaseRecommendationService` to persist `READY_FOR_DISPATCH` (`SUCCESS` / `PARTIAL_SUCCESS`) or `SUPPRESSED` (`NO_ELIGIBLE_RECOMMENDATIONS` / `NO_MAPPED_SOURCE_PRODUCTS`) campaigns with full `decision_trace` and `metadata_provenance` without dispatching outbound WhatsApp messages.
  - 122 automated pytest tests passing on both host and Docker against PostgreSQL.
- **Stage 6 (WhatsApp Provider Integration & Interactive Simulator)**:
  - Common WhatsApp provider interface with two implementations: `SimulatorWhatsAppProvider` (local demo mode with `[SIMULATED]` provenance) and `MetaCloudWhatsAppProvider` (production HTTP client for Meta WhatsApp Cloud API with timeout, error classification, and secret redaction).
  - Outbound dispatch coordinator with short database transactions (row locked and transitioned to `DISPATCHING` before HTTP call; outcome persisted in short Tx 2).
  - Status progression lifecycle: `READY_FOR_DISPATCH -> DISPATCHING -> PROVIDER_ACCEPTED -> DELIVERY_PENDING -> DELIVERED -> READ`; `SENT_SIMULATED` for simulator.
  - Signed Meta status webhooks (`GET` verification handshake & `POST` `X-Hub-Signature-256` HMAC-SHA256 verification) with event deduplication and regression prevention.
- **Stage 7 (Plural Commerce Automation Frontend Dashboard)**:
  - Polished SaaS business dashboard with dark navy sidebar, clean white cards, and responsive layout served at `http://localhost:8000/` and `http://localhost:8000/simulator`.
  - 6 core navigation views: Overview (authoritative KPIs), Campaigns (filters, decision trace drawer, dispatch guard), WhatsApp Inbox (3-column Squarebox-inspired interactive conversation simulator), Orders & Customers (line items, consent provenance, masked phones), Product Catalogue (70 SKUs, Rx status, metadata provenance, 6 ranked mappings modal), and Settings (integration status, provider health, API verification).
  - 154 automated pytest tests passing on both host and Docker.

## Key architectural & algorithm decisions (MVP)

- **Equal allocation (conditional quantity tie-breaking)**: Distribute up to six recommendation slots equally across distinct mapped ordered SKUs (`base_quota = 6 // M`, `remainder = 6 % M`). `quantity_sum` is used **only** as a tie-breaker for source prioritization when **more than six** mapped source SKUs exist (`M > 6`); for `M <= 6`, `quantity_sum` is not used so quantity never influences remainder slot allocation when all mapped sources can participate. Equal allocation remains the MVP rule.
- **No arbitrary first-six truncation**: For orders with more than six mapped SKUs (`M > 6`), `base_quota = 0` and `remainder = 6`. All `M` mapped sources undergo consistent eligibility evaluation before prioritization and are ranked via the conditional deterministic source priority key rather than arbitrarily truncating to the first six order lines.
- **Deterministic and explainable source prioritization**: All mapped source products undergo consistent candidate eligibility evaluation before prioritization, and are then prioritized using a conditional priority key based on `M`:
  - **For `M <= 6` mapped source SKUs**:
    `priority_key = (-eligible_candidate_count, first_line_index, product_code)`
    (`eligible_candidate_count` descending → `first_line_index` ascending → `product_code` ascending)
  - **For `M > 6` mapped source SKUs**:
    `priority_key = (-eligible_candidate_count, -quantity_sum, first_line_index, product_code)`
    (`eligible_candidate_count` descending → `quantity_sum` descending → `first_line_index` ascending → `product_code` ascending)
  Recommendation provenance (`source_product_code` and original `mapping_rank`) is preserved for every recommended item in the decision trace.
- **Strict filtering & fewer than six return contract**: Return fewer than six recommendations when eligible candidates are exhausted. Never relax Rx, stock, active, purchased-item, or duplicate constraints to pad to six.
- **Global bestseller fallback**: Explicitly deferred as a **future enhancement only** (not active in the MVP).
- **WhatsApp provider & failure semantics**:
  - **Meta WhatsApp Cloud API** is the **primary provider**; the **WhatsApp simulator** is the **fallback/demo provider**.
  - **Do not automatically switch to the simulator after a real Meta API delivery failure**, as that would create a false impression that the customer received a message.
  - **Explicit simulator mode or unavailable credentials**: the simulator can be selected.
  - **Meta API transient failure**: retry safely and record the failure/pending state without falling over to the simulator.
  - **Meta API acceptance**: record provider acceptance (`PROVIDER_ACCEPTED`), not confirmed delivery.
  - **Actual delivery confirmation**: transition to confirmed delivery (`DELIVERED`) via Meta webhook status updates.

## Files

- `Business_A_Synthetic_Product_Mapping_No_Quantity.xlsx` / `data/raw/product_mapping_source.xlsx` — original source workbook copies (preserved unchanged).
- `data/normalized/product_mappings.json` — normalized source → ranked target mappings (preserved unchanged).
- `data/normalized/product_mappings.csv` — flat mapping rows for inspection (preserved unchanged).
- `data/normalized/validation_report.md` / `.json` — validation result and source limitations.
- `docs/requirements_and_assumptions.md` — finalized requirements, MVP architectural decisions, and future enhancements.
- `docs/recommendation_algorithm.md` — deterministic algorithm specification with explainable source prioritization.
- `docs/test_cases.csv` / `.md` — 24 acceptance test cases aligned with finalized MVP rules.
- `docs/catalogue_seeding_and_integrity.md` — Stage 3 catalogue loader, metadata provenance, idempotency, and verification guide.
- `data/fixtures/synthetic_catalogue_metadata.json` — explicitly labelled synthetic demonstration catalogue metadata fixture.
- `scripts/validate_dataset.py` — Stage 1 dataset validation and normalization script.
- `backend/app/services/catalogue_loader.py` — Stage 3 catalogue and mapping validation, loading, and verification service.
- `backend/app/scripts/seed_catalogue.py` & `validate_database_catalogue.py` — CLI entrypoints for seeding and auditing PostgreSQL.
- `backend/app/services/recommendation/` — Stage 4 pure deterministic recommendation engine (`models.py`, `eligibility.py`, `allocator.py`, `trace.py`, `engine.py`).
- `backend/app/schemas/recommendation.py` — Stage 4 Pydantic v2 schemas for recommendation inputs, outputs, provenance, and decision traces.
- `backend/tests/recommendation/` — Stage 4, 4.1 & 4.2 test suite covering recommendation scenarios, trace accounting, and PostgreSQL metadata provenance.
- `backend/app/services/campaign_orchestrator.py` — Stage 5 order-delivery event processing, customer consent evaluation, and campaign orchestration service.
- `backend/app/api/routes/webhooks.py` — Stage 5 `POST /api/v1/webhooks/order-delivered` endpoint with configurable webhook authentication.
- `backend/app/schemas/campaign.py` — Stage 5 Pydantic v2 schemas for delivery-event payloads and campaign orchestration responses.
- `backend/tests/test_stage5_campaign_orchestration.py` — Stage 5 test suite covering all 33 event validation, idempotency, concurrency, consent, provenance, and campaign lifecycle scenarios.
- `docs/order_delivery_and_campaign_orchestration.md` — Stage 5 architectural and operational reference.

## Important data caveat

The source workbook is a mapping file, not a full product catalogue or order dataset. It does not contain authoritative stock, active status, Rx status, quantities, price, images, or customer consent. By default, workbook-seeded products preserve unknown eligibility metadata (`is_rx = NULL`, `is_active = NULL`, `available_quantity = NULL`, `metadata_source = WORKBOOK_ONLY`) so unknown metadata fails closed (`CATALOGUE_METADATA_MISSING`). Demonstration metadata is kept in a separate, explicitly tagged synthetic fixture (`data/fixtures/synthetic_catalogue_metadata.json`, `metadata_source = SYNTHETIC_FIXTURE`, `is_synthetic_metadata = true`) and never overwrites verified catalogue records (`VERIFIED_CATALOGUE`).

## Stage 6 — WhatsApp providers & simulator
See [docs/whatsapp_provider_and_simulator.md](docs/whatsapp_provider_and_simulator.md).

## Stage 7 — Plural Commerce Automation Frontend Dashboard
See [docs/frontend_dashboard.md](docs/frontend_dashboard.md).
- Open **http://localhost:8000/** (or `http://localhost:8000/simulator`) in any modern browser.
- Click **"Load Demo Data"** in the top bar to populate realistic demo walkthrough data across all views.
- Navigate across **Overview**, **Campaigns**, **WhatsApp Inbox**, **Orders & Customers**, **Product Catalogue**, and **Settings**.
- All interactive customer actions (e.g. *View Details*, *Interested*, *Pass*) and campaign dispatches persist directly to the PostgreSQL database.

## Stage 8 — Ecommerce Storefront + Automated Delivery Journey + Real WhatsApp Cloud API
See [docs/stage8_storefront_and_meta_journey.md](docs/stage8_storefront_and_meta_journey.md).
- **Customer Storefront (`/store`)**: Live responsive storefront browsing all 70 catalogue products with real-time category filtering, search, and dynamic cart drawer.
- **Strict Compliance & Consent**: Checkout enforces strict E.164 phone numbers (`^\+[1-9]\d{7,14}$`) and an explicit, unchecked-by-default WhatsApp marketing opt-in checkbox with full provenance recording (`consent_source = "STOREFRONT_CHECKOUT"`, `consent_recorded_at`).
- **Automated Demo Delivery Progression**: Background scheduler and worker (`DemoDeliveryWorker`) progresses demo orders through `PLACED -> CONFIRMED -> PACKED -> OUT_FOR_DELIVERY -> DELIVERED` across a configurable 10–15s window (default 12s, gated by `DEMO_DELIVERY_ENABLED`).
- **Post-Delivery Recommendation Trigger**: Reaching `DELIVERED` automatically triggers Stage 5 event orchestration and Stage 6 WhatsApp dispatch coordinator without holding database locks across HTTP.
- **Inbound Customer WhatsApp Replies**: Meta webhook routes ingest customer messages (`messages[]`), deduplicate by `wamid`, persist in `whatsapp_messages`, and execute deterministic keyword actions:
  - `STOP` / `UNSUBSCRIBE`: Sets `customer.opted_out = True`, `whatsapp_opt_in = False`, records `opt_out_recorded_at`, suppresses future campaigns, and dispatches confirmation.
  - `START` / `RESUBSCRIBE`: Resubscribes customer with fresh consent audit record.
  - `HELP` / `SUPPORT`: Dispatches customer support assistance information.
- **161 Automated Tests**: 100% test pass rate on host and Docker.

