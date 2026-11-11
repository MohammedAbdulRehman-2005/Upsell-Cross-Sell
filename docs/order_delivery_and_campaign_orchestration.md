# Stage 5 — Order Delivery Event Processing & Campaign Orchestration

## 1. Overview

Stage 5 implements the transactional order-delivery event processing and campaign orchestration layer that connects persisted customer orders in PostgreSQL to the approved Stage 4 deterministic recommendation engine.

### Stage 5 Architectural Boundary

Stage 5 **prepares and persists** upsell/cross-sell campaign records (`MessageLog`) in response to verified `order.delivered` events, but **does not dispatch** outbound WhatsApp messages:

- Eligible campaigns with 1–6 recommendations are persisted with `delivery_status = READY_FOR_DISPATCH`.
- Campaigns blocked by customer consent, opt-out, invalid/missing destination phone, or zero eligible recommendations are persisted with `delivery_status = SUPPRESSED` and a canonical `suppression_reason`.
- Transient orchestration errors are persisted with `delivery_status = FAILED_RETRYABLE` and can be safely recovered on retry without creating duplicate campaign rows.
- Unrecoverable persisted order data errors are persisted with `delivery_status = FAILED_PERMANENT`.
- **No record in Stage 5 is ever marked `PROVIDER_ACCEPTED`, `SENT_SIMULATED`, or `DELIVERED`.** Outbound provider dispatch and delivery status webhooks belong to Stage 6.

---

## 2. Inbound Order-Delivery Event Contract

### Endpoint

`POST /api/v1/webhooks/order-delivered`

### Request Schema (`OrderDeliveredWebhookRequest`)

```json
{
  "event_id": "evt_20261111_001",
  "event_type": "order.delivered",
  "order_id": "ORD-5001",
  "occurred_at": "2026-11-11T12:05:00Z",
  "source": "order_service",
  "metadata": {
    "courier": "BlueDart"
  }
}
```

### Event Validation & Security Rules

1. **Webhook Authentication (`verify_order_webhook_auth`)**:
   - `order_webhook_auth_mode = "dev_insecure_open"`: Allowed **only** when `environment` is `development` or `testing`. If configured in `staging` or `production`, the endpoint fails closed with `HTTP 503 Service Unavailable`.
   - `order_webhook_auth_mode = "shared_token_header"`: Verifies the `X-Webhook-Token` request header against `ORDER_WEBHOOK_SHARED_TOKEN` using constant-time `hmac.compare_digest`. Missing or invalid tokens are rejected with `HTTP 401 Unauthorized`.
2. **Event Type Verification**:
   - Only `event_type == "order.delivered"` (or `"ORDER_DELIVERED"`) is accepted. Any other event type is rejected with `HTTP 422` (`outcome = REJECTED_UNSUPPORTED_EVENT_TYPE`).
3. **Authorized Event Source Verification**:
   - `source` must match `settings.allowed_delivery_event_sources` (default: `["order_service", "logistics_webhook", "oms", "manual_trigger"]`). Unauthorized sources are rejected with `HTTP 403 Forbidden` (`outcome = REJECTED_UNAUTHORIZED_SOURCE`).
4. **Privacy-Safe Audit Persistence**:
   - Inbound payloads stored in `webhook_events.payload` are sanitized via `sanitize_event_payload_for_audit`: phone numbers are masked (`+91******3210`) and secret/token keys (`token`, `secret`, `password`, `api_key`, `authorization`) are redacted (`***REDACTED***`).

---

## 3. Authoritative Order-State Verification

An inbound webhook payload **never** overrides the persisted order state in PostgreSQL:

| Persisted `orders.order_status` | Event Outcome | Campaign Created? | HTTP Status |
|---|---|---|---|
| Order not found in DB | `REJECTED_ORDER_NOT_FOUND` | No | `404 Not Found` |
| Event `customer_id` mismatches DB `customer_id` | `REJECTED_ORDER_CUSTOMER_MISMATCH` | No | `409 Conflict` |
| `PENDING`, `PROCESSING`, `SHIPPED` | `REJECTED_ORDER_NOT_DELIVERED` | No | `200 OK` |
| `CANCELLED` | `REJECTED_ORDER_CANCELLED` | No | `200 OK` |
| `DELIVERED` | Proceeds to idempotency, consent & recommendation gates | Yes (`READY_FOR_DISPATCH` or `SUPPRESSED`) | `200 OK` |

Even if the inbound event payload includes `"order_status": "DELIVERED"`, a persisted order in `PENDING`, `PROCESSING`, `SHIPPED`, or `CANCELLED` state will never trigger a campaign.

---

## 4. Multi-Layer Idempotency & Concurrency Control

Stage 5 prevents duplicate processing and race conditions across three database-enforced layers:

1. **Layer 1 — Event-Level Uniqueness (`webhook_events.event_id` UNIQUE)**:
   - Repeating the same `event_id` returns `outcome = DUPLICATE_EVENT_NOOP` (`idempotent_replay = True`) with the existing campaign state.
   - Concurrent requests with the exact same `event_id` use a PostgreSQL savepoint (`session.begin_nested()`); the losing transaction catches `IntegrityError` and returns `DUPLICATE_EVENT_NOOP`.
2. **Layer 2 — Row-Level Locking on Persisted Order (`SELECT ... FOR UPDATE`)**:
   - `OrderRepository.get_by_external_id_for_update` acquires a PostgreSQL row-level `FOR UPDATE` lock on the `orders` row.
   - Concurrent delivery events with *different* `event_id`s targeting the same `order_id` are serialized at the database lock boundary.
3. **Layer 3 — Campaign-Level Uniqueness (`uq_message_logs_order_campaign` on `(order_id, campaign_type)` + `idempotency_key` UNIQUE)**:
   - Before evaluating consent or recommendations, the orchestrator checks `MessageLogRepository.get_by_order_and_campaign(order.id, campaign_type)`.
   - If a non-retryable campaign already exists, the new event is recorded as `IGNORED` in `webhook_events` and returns `outcome = DUPLICATE_ORDER_CAMPAIGN_NOOP` (`idempotent_replay = True`).
   - If the existing campaign is in `FAILED_RETRYABLE` state, the retry updates the existing `MessageLog` row in-place and increments `retry_count`.
4. **Layer 4 — Atomic Transaction Rollback & Safe Recovery**:
   - Any unhandled exception during event orchestration or campaign persistence triggers `session.rollback()`, removing all uncommitted `WebhookEvent` and `MessageLog` writes from the failed transaction so no event is falsely marked processed and no partial campaign record remains.
   - Re-submitting the same `event_id` after a rolled-back transaction or `FAILED_RETRYABLE` attempt acquires a `FOR UPDATE` row lock on both `WebhookEvent` and `Order`, ensuring sequential and concurrent retries recover cleanly into a single campaign record.

---

## 5. Technical Customer Consent, Opt-Out & Strict E.164 Destination Gate

Customer eligibility is evaluated in strict fail-closed order (`evaluate_customer_messaging_eligibility`) before recommendations are generated:

| Priority | Condition | `delivery_status` | `suppression_reason` |
|---|---|---|---|
| 1 | `phone_number` is `NULL`, empty (`""`), or whitespace-only | `SUPPRESSED` | `CUSTOMER_PHONE_MISSING` |
| 2 | `phone_number` fails strict E.164 regex `^\+[1-9]\d{7,14}$` (leading `+` mandatory, 8–15 digits, no leading `0`; never auto-prepends a country code) | `SUPPRESSED` | `INVALID_WHATSAPP_DESTINATION` |
| 3 | `whatsapp_opt_in IS NULL` or `opted_out IS NULL` | `SUPPRESSED` | `CONSENT_STATUS_UNKNOWN` |
| 4 | `opted_out IS TRUE` (always overrides `whatsapp_opt_in = True`) | `SUPPRESSED` | `CUSTOMER_OPTED_OUT` |
| 5 | `whatsapp_opt_in IS FALSE` | `SUPPRESSED` | `WHATSAPP_MARKETING_CONSENT_MISSING` |
| 6 | Technical consent provenance gate (`require_consent_audit_metadata=True`): missing `consent_source` or `consent_recorded_at` | `SUPPRESSED` | `CONSENT_STATUS_UNKNOWN` |

### Environment Defaults for `require_consent_audit_metadata`

| Environment | Default `require_consent_audit_metadata` | Override Policy |
|---|---|---|
| `development` | `False` | Can be explicitly set to `True` |
| `testing` | `False` | Can be explicitly set to `True` |
| `staging` | `True` | Enforced fail-closed; setting `False` raises `ValidationError` |
| `production` | `True` | Enforced fail-closed; setting `False` raises `ValidationError` |

---

## 6. Recommendation Engine Integration & Metadata Provenance

When a delivered order and its customer pass all eligibility checks, `CampaignOrchestrationService` invokes `DatabaseRecommendationService.generate_for_order_input` using the authoritative persisted `order_line_items`:

| Stage 4 `RecommendationResultStatus` | Selected Count | Stage 5 `delivery_status` | `suppression_reason` |
|---|---|---|---|
| `SUCCESS` | 6 | `READY_FOR_DISPATCH` | `None` |
| `PARTIAL_SUCCESS` | 1–5 (never padded) | `READY_FOR_DISPATCH` | `None` |
| `NO_ELIGIBLE_RECOMMENDATIONS` | 0 | `SUPPRESSED` | `NO_ELIGIBLE_RECOMMENDATIONS` |
| `NO_MAPPED_SOURCE_PRODUCTS` | 0 | `SUPPRESSED` | `NO_MAPPED_SOURCE_PRODUCTS` |

Each campaign record (`message_logs`) persists:
- `order_id`, `customer_id`, `campaign_type`, `trigger_event_id`
- `delivery_status`, `recommendation_status`, `suppression_reason`
- `recommended_skus` (in exact deterministic selection order)
- `decision_trace` (complete Stage 4.1 reconciled decision trace JSON)
- `metadata_provenance` (`evaluated_product_count`, `selected_product_count`, `source_counts`, `selected_source_counts`, `contains_synthetic_metadata`, `contains_workbook_only_metadata`)
- `request_payload` (prepared Stage 6 dispatch payload for `READY_FOR_DISPATCH` campaigns)
