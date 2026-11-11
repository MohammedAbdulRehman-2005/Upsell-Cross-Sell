# Stage 6 - WhatsApp Provider Integration & Customer Simulator

## Flow
`order.delivered` webhook (Stage 5) -> campaign `READY_FOR_DISPATCH` -> **DispatchCoordinator** -> provider -> status.

## Providers (`WHATSAPP_PROVIDER`)
| Mode | Behaviour |
|---|---|
| `simulator` (default) | No credentials. Messages stored in `simulator_messages`, labelled SIMULATED. Final status `SENT_SIMULATED` (never `DELIVERED`). |
| `meta_cloud_api` | Real `httpx` call to `POST {WHATSAPP_BASE_URL}/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages` with an approved template. Missing credentials -> `FAILED_PERMANENT` / `PROVIDER_NOT_CONFIGURED`. |

**The system never falls back to the simulator after a Meta failure.** The simulator runs only when explicitly selected.

## Dispatch transaction model
1. Tx 1: `SELECT ... FOR UPDATE`, validate status/phone/recommendations, set `DISPATCHING`, **commit** (lock released).
2. Provider HTTP call, outside any DB transaction.
3. Tx 2: lock row, persist outcome (status, provider id, error code, timestamps, simulator message).

Concurrent dispatch attempts: exactly one provider call (tested on PostgreSQL with 5 threads).

## Status lifecycle & Meta error mapping
`READY_FOR_DISPATCH -> DISPATCHING -> PROVIDER_ACCEPTED -> DELIVERY_PENDING -> DELIVERED -> READ`; `SENT_SIMULATED` (simulator only).

| Condition | Status |
|---|---|
| HTTP 200 with message id | `PROVIDER_ACCEPTED` (acceptance, **not** delivery) |
| 401/403/OAuth 190 | `FAILED_PERMANENT` |
| 429 / rate-limit codes | `FAILED_RETRYABLE` |
| 5xx, connect error, connect timeout | `FAILED_RETRYABLE` |
| Invalid template / recipient | `FAILED_PERMANENT` |
| Read/write timeout (request may have been sent) | `DELIVERY_UNKNOWN` (no blind re-send) |

`FAILED_RETRYABLE` campaigns are re-dispatched only with `force_retry=true`. `SUPPRESSED`, `FAILED_PERMANENT`, `DELIVERY_UNKNOWN` are never dispatched.

## Webhooks
- `GET /api/v1/webhooks/whatsapp`: `hub.mode=subscribe` + `hub.verify_token` check, returns `hub.challenge`.
- `POST /api/v1/webhooks/whatsapp`: `X-Hub-Signature-256` HMAC-SHA256 with `WHATSAPP_APP_SECRET`. Fails closed in `staging`/`production` without a secret. Only `statuses[]` are used; customer `messages[]` are never treated as delivery evidence. Events deduplicated via `webhook_events.event_id`; unknown `provider_message_id`s ignored; status only moves forward; simulated campaigns can never be set `DELIVERED`.

## Simulator
`GET /simulator` (also `/`). Campaign list, chat bubbles (SIMULATED label), recommendation cards, interactive actions (`VIEW_RECOMMENDATION`, `INTERESTED`, `NOT_INTERESTED`, `VIEW_ALL_RECOMMENDATIONS`), provider status, decision trace, dispatch button, "Load demo data" (`POST /api/v1/simulator/demo-seed`, disabled in staging/production; uses synthetic catalogue metadata).

## API
`GET /api/v1/campaigns`, `GET /api/v1/campaigns/{id}`, `POST /api/v1/campaigns/{id}/dispatch`, `GET /api/v1/campaigns/provider-status`, `GET|POST /api/v1/simulator/campaigns/{id}/messages|interact`.

## Configuration (see `.env.example`)
`WHATSAPP_PROVIDER`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_BUSINESS_ACCOUNT_ID`, `WHATSAPP_API_VERSION`, `WHATSAPP_BASE_URL`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_TEMPLATE_NAME`, `WHATSAPP_TEMPLATE_LANGUAGE`, `WHATSAPP_HTTP_TIMEOUT_SECONDS`. Secrets are `SecretStr`, redacted from errors/logs.

## Known limits
- Real Meta delivery was **not** exercised against live Meta (no credentials); verified with `httpx.MockTransport`.
- The Meta template body assumes two text parameters (order reference, recommendation summary); the real approved template must match.
- Simulator customer actions are recorded as conversation only; no orders are created.
