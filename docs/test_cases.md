# Test case matrix

| ID | Scenario | Expected result | Category |
|---|---|---|---|
| T01 | Single mapped ordered SKU | Return six ranked unique recommendations. | Happy path |
| T02 | Two mapped ordered SKUs | Equal allocation (3 initial slots per source), then priority-ordered backfill if needed. | Happy path |
| T03 | Three mapped ordered SKUs | Equal allocation of 2 slots per source; `M <= 6` deterministic source priority order (`eligible_candidate_count` desc, `first_line_index` asc, `product_code` asc). | Allocation |
| T04 | Four mapped ordered SKUs | Equal allocation of 2,2,1,1 assigned in `M <= 6` deterministic source priority order (`eligible_candidate_count` desc, `first_line_index` asc, `product_code` asc) without quantity influencing remainder slots. | Allocation |
| T05 | More than six mapped ordered SKUs | No arbitrary first-six truncation; evaluate eligibility across all mapped sources first, rank by `M > 6` deterministic source priority (`eligible_candidate_count` desc, `quantity_sum` desc, `first_line_index` asc, `product_code` asc), assign 1 equal initial slot to top six prioritized sources, and include all mapped sources in round-robin backfill. | Source prioritization / allocation |
| T06 | Repeated SKU order lines | Aggregate to one source SKU; do not give it multiple base quotas; `quantity_sum` is used only as a source-prioritization tie-breaker when `M > 6` (never influences `M <= 6` remainder allocation or slot weighting) and is retained in trace. | Normalization |
| T07 | Purchased target exclusion | Exclude purchased target regardless of source mapping rank. | Filtering |
| T08 | Cross-source duplicate candidate | Return target once; retain first deterministic provenance; backfill if needed. | Deduplication |
| T09 | Unavailable candidate | Exclude and scan next ranked candidate. | Eligibility / backfill |
| T10 | Inactive candidate | Exclude and scan next ranked candidate. | Eligibility / backfill |
| T11 | Rx candidate | Always exclude; never recommend. | Safety / eligibility |
| T12 | Unknown catalogue metadata | Fail closed for candidate; record exclusion reason. | Validation |
| T13 | One source lacks mapping | Use mapped source(s); redistribute quota equally across mapped sources; do not fail whole order. | Missing mapping |
| T14 | No source has mapping | Return empty result and do not send WhatsApp message. | Missing mapping |
| T15 | Fewer than six eligible candidates | Return four when eligible candidates are exhausted; do not relax filters, invent products, or invoke global bestseller fallback (future enhancement only). | Output contract |
| T16 | No eligible candidates | Return empty result; do not send recommendation message. | Empty outcome |
| T17 | Order not delivered | Do not compute/send recommendations. | Trigger gate |
| T18 | Duplicate delivered webhook | Only one dispatch; second event is idempotent no-op. | Reliability |
| T19 | Customer opted out / no consent | Compute may be retained for audit, but do not dispatch message. | Messaging policy |
| T20 | WhatsApp provider failure, acceptance & simulator selection semantics | Do not auto-switch to simulator on real Meta API failure; retry Meta transient failure safely and record failed/pending state; record Meta API acceptance (`PROVIDER_ACCEPTED`) separately from Meta webhook delivery confirmation (`DELIVERED`); select simulator only in explicit simulator mode or when credentials are unavailable. | Integration |
| T21 | Malformed order payload | Reject with validation error; no recommendation or dispatch. | Input validation |
| T22 | Mapping file malformed | Fail dataset load with actionable validation errors. | Dataset validation |
| T23 | Stable ordering | Identical source priority order, product selection order, and preserved provenance (`source_product_code`, original `mapping_rank`) across repeated runs. | Determinism |
| T24 | Rank preservation | Lower mapping rank considered before higher rank within each prioritized source. | Ranking |

## Stage 4 Automated Recommendation Engine Test Matrix (40 Scenarios)

Implemented and verified in `backend/tests/recommendation/`:

| Scenario # | Description | Test Module | Expected Status / Reason Code |
|---:|---|---|---|
| 1–7 | 1, 2, 3, 4, 5, 6, and 7 (`>6`) mapped source SKUs equal slot allocation (`[6]`, `[3,3]`, `[2,2,2]`, `[2,2,1,1]`, `[2,1,1,1,1]`, `[1,1,1,1,1,1]`, `[1,1,1,1,1,1,0]`) | `test_engine_basic.py` | `SUCCESS` (`INITIAL_ALLOCATION`) |
| 8–15 | Eligibility filtering: Rx (`RX_PRODUCT_EXCLUDED`), inactive (`PRODUCT_INACTIVE`), zero/negative stock (`PRODUCT_UNAVAILABLE`), unknown Rx/active/stock (`CATALOGUE_METADATA_MISSING`), and multi-reason preservation | `test_eligibility.py` | Fail-closed exclusion & deterministic multi-reason list |
| 16–18 | Repeated SKU line aggregation (`quantity_sum`, earliest `first_line_index`), single & multiple purchased SKU exclusion (`ALREADY_PURCHASED`) | `test_order_normalization.py` | Single source quota; purchased SKUs excluded |
| 19–22 | Unmapped source alongside mapped source (`NO_MAPPING_FOUND`), all sources unmapped (`NO_MAPPED_SOURCE_PRODUCTS`), one source 0 eligible with backfill, all sources 0 eligible (`NO_ELIGIBLE_RECOMMENDATIONS`) | `test_edge_cases.py` | `SUCCESS`, `NO_MAPPED_SOURCE_PRODUCTS`, `NO_ELIGIBLE_RECOMMENDATIONS` |
| 23–24, 27–32 | Priority tie-breakers (`eligible_candidate_count` desc, conditional `quantity_sum` desc only when `M > 6`, `first_line_index` asc, `product_code` asc), initial shortfall + round-robin `BACKFILL`, and `>6` non-initial source backfill | `test_allocation.py` | `INITIAL_ALLOCATION` + `BACKFILL` |
| 25–26, 38 | Cross-source duplicate during `INITIAL_ALLOCATION` and `BACKFILL`, trace provenance (`DUPLICATE_RECOMMENDATION`, retained source/rank/sequence) | `test_deduplication.py` | `DUPLICATE_RECOMMENDATION` |
| 33 | Exhaustion before 6 slots filled (e.g., 2 sources in same 7-SKU isolated mapping cluster returning 5 items) | `test_edge_cases.py` | `PARTIAL_SUCCESS` (`unfilled_slots = 1`) |
| 34–37 | Original rank (`1..6`) preserved when earlier ranks skipped, source attribution preserved, `INITIAL_ALLOCATION` vs `BACKFILL` recorded, all rejection reasons validated via Pydantic v2 schema | `test_provenance.py` | Full provenance & trace validation |
| 39–40 | Byte-for-byte canonical JSON determinism and 50x shuffled input permutation invariance | `test_determinism.py` | 100% deterministic output |
| 41–44 (Stage 4.1 Audit) | Trace counter reconciliation (`eligibility_evaluations_count` vs `allocation_candidates_examined` vs `unvisited_candidate_count`), `M = 2` different-quantity invariance (`[3, 3]`), and end-to-end PostgreSQL order loading + batch SQL query verification | `test_stage4_1_audit.py` | Reconciled trace counters & verified PostgreSQL batch execution |
| 45–47 (Stage 4.2 Provenance) | Explicit PostgreSQL verification of `WORKBOOK_ONLY` (70 rows, NULL eligibility -> `NO_ELIGIBLE_RECOMMENDATIONS`), `SYNTHETIC_FIXTURE` (70 rows, 0 `VERIFIED_CATALOGUE` -> `SUCCESS` with synthetic provenance), and `VERIFIED_CATALOGUE` non-overwrite safety across repeated seeds | `test_stage4_2_metadata_provenance_pg.py` | Verified PostgreSQL provenance separation & idempotency |

## Stage 5 Order-Delivery Event Processing & Campaign Orchestration Test Matrix (33 Scenarios)

Implemented and verified in `backend/tests/test_stage5_campaign_orchestration.py`:

| Scenario # | Description | Category | Expected Outcome / Status |
|---:|---|---|---|
| 1 | Valid `order.delivered` event for eligible delivered order | Event validation | `CAMPAIGN_READY_FOR_DISPATCH` / `READY_FOR_DISPATCH` (`HTTP 200`) |
| 2 | Invalid event type (`order.created`) | Event validation | `REJECTED_UNSUPPORTED_EVENT_TYPE` (`HTTP 422`), 0 campaigns created |
| 3 | Missing required event fields (`event_id`, `event_type`, `order_id`, `occurred_at`, `source`) | Event validation | Pydantic validation failure (`HTTP 422`) |
| 4 | Unknown `order_id` | Event validation | `REJECTED_ORDER_NOT_FOUND` (`HTTP 404`), 0 campaigns created |
| 5 | Order in `PENDING`, `PROCESSING`, or `SHIPPED` state | Order state gate | `REJECTED_ORDER_NOT_DELIVERED` (`HTTP 200`), 0 campaigns created |
| 6 | Order in `CANCELLED` state | Order state gate | `REJECTED_ORDER_CANCELLED` (`HTTP 200`), 0 campaigns created |
| 7 | Event claiming `DELIVERED` when persisted order is `SHIPPED` | Authoritative DB state | `REJECTED_ORDER_NOT_DELIVERED` (`HTTP 200`), 0 campaigns created |
| 8 | Event from unauthorized source + webhook auth modes (`dev_insecure_open` blocked in `production`, `shared_token_header` HMAC check) | Webhook security | `REJECTED_UNAUTHORIZED_SOURCE` (`403`), auth failure (`401`/`503`) |
| 9 | Duplicate delivery event with identical `event_id` | Idempotency | `DUPLICATE_EVENT_NOOP` (`idempotent_replay=True`), 1 campaign row |
| 10 | Duplicate delivery event with different `event_id` for the same order | Idempotency | `DUPLICATE_ORDER_CAMPAIGN_NOOP` (`idempotent_replay=True`), 1 campaign row |
| 11 | Concurrent duplicate delivery events against PostgreSQL (6 concurrent distinct `event_id`s + 6 concurrent identical `event_id`s) | Concurrency | Row lock (`FOR UPDATE`) + unique constraints ensure 1 campaign per order |
| 12 | Retry after transient failure (`FAILED_RETRYABLE`) without duplicate campaign creation | Fault recovery | In-place transition `FAILED_RETRYABLE` -> `READY_FOR_DISPATCH`, `retry_count=1` |
| 13 | Transaction rollback on injected PostgreSQL campaign persistence failure, followed by same-event retry recovery, idempotent replay, and 6-worker concurrent retry verification | Transactional safety | Full rollback leaving 0 partial rows; retry recovers to 1 `READY_FOR_DISPATCH` campaign; replay & concurrent retries create 0 duplicates |
| 14 | Opted-in customer with valid strict E.164 WhatsApp phone number (`+919876543210`, `+12345678`, `+123456789012345`) | Consent & destination | Eligible -> `READY_FOR_DISPATCH` |
| 15 | Customer without marketing consent (`whatsapp_opt_in = False`) | Consent & destination | `SUPPRESSED` (`WHATSAPP_MARKETING_CONSENT_MISSING`) |
| 16 | Customer explicitly opted out (`opted_out = True`, overrides `whatsapp_opt_in = True`) | Consent & destination | `SUPPRESSED` (`CUSTOMER_OPTED_OUT`) |
| 17 | Customer with unknown/NULL consent (`whatsapp_opt_in IS NULL` or `opted_out IS NULL`) + `production`/`staging` technical consent provenance gate (`require_consent_audit_metadata=True`, cannot be disabled in `production`/`staging`) | Consent & destination | Fail-closed `SUPPRESSED` (`CONSENT_STATUS_UNKNOWN`) |
| 18 | Customer with missing, blank, or whitespace-only phone number (`None`, `""`, `"   "`, `"\t\n"`) | Consent & destination | `SUPPRESSED` (`CUSTOMER_PHONE_MISSING`) |
| 19 | Customer with invalid phone number under strict E.164 `^\+[1-9]\d{7,14}$` (missing `+`, leading zero, `<8` digits, `>15` digits, non-digits; never auto-prepends country code) | Consent & destination | `SUPPRESSED` (`INVALID_WHATSAPP_DESTINATION`) |
| 20 | Order producing 6 recommendations (`SUCCESS`) | Recommendation integration | `READY_FOR_DISPATCH`, 6 SKUs preserved |
| 21 | Order producing 1–5 recommendations (`PARTIAL_SUCCESS`) | Recommendation integration | `READY_FOR_DISPATCH`, 3 SKUs preserved without padding |
| 22 | Order producing `NO_ELIGIBLE_RECOMMENDATIONS` | Recommendation integration | `SUPPRESSED` (`NO_ELIGIBLE_RECOMMENDATIONS`) |
| 23 | Order producing `NO_MAPPED_SOURCE_PRODUCTS` | Recommendation integration | `SUPPRESSED` (`NO_MAPPED_SOURCE_PRODUCTS`) |
| 24 | Workbook-only metadata in PostgreSQL (`WORKBOOK_ONLY`) | Provenance integration | `NO_ELIGIBLE_RECOMMENDATIONS` -> `SUPPRESSED` (`contains_workbook_only_metadata=True`) |
| 25 | Synthetic fixture metadata in PostgreSQL (`SYNTHETIC_FIXTURE`) | Provenance integration | `READY_FOR_DISPATCH` with `SYNTHETIC_FIXTURE` provenance (`contains_synthetic_metadata=True`) |
| 26 | Verified catalogue metadata (`VERIFIED_CATALOGUE`) | Provenance integration | `READY_FOR_DISPATCH` with `VERIFIED_CATALOGUE` provenance (`contains_synthetic_metadata=False`) |
| 27–33 | Full `decision_trace` persisted, ordered `recommended_skus` preserved, `suppression_reason` persisted, `trigger_event_id` linked, payload privacy-sanitized, and zero records marked `PROVIDER_ACCEPTED`, `SENT_SIMULATED`, or `DELIVERED` | Lifecycle & boundaries | Verified on persisted PostgreSQL rows |

## Stage 6 tests (backend/tests/test_stage6_whatsapp_and_simulator.py)
Simulator dispatch + persistence; duplicate dispatch no-op; non-dispatchable statuses; invalid phone; DISPATCHING committed before provider call; Meta accepted != delivered; Meta 401/429/500/template/recipient mapping with secret redaction and no simulator fallback; read-timeout -> DELIVERY_UNKNOWN; retry flag; missing credentials; webhook challenge; signature rejection; progression/dedup/no regression; unknown id; customer messages not trusted; simulated never DELIVERED; fail-closed signature in staging; interactions chronological; demo seed idempotent; PostgreSQL concurrent dispatch sends once.

