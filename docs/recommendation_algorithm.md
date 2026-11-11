# Recommendation algorithm specification

## Inputs

- `order_id`, `customer_id`, `order_status`, ordered lines (`product_code`, optional `quantity` defaulting to `1`, stable `line_index`)
- Normalized mapping table: source SKU → up to six ranked target SKUs (`rank` 1..6)
- Product catalogue metadata: `is_rx`, `is_active`, `available_quantity` (plus display fields)
- Customer messaging consent/opt-out state (`has_consent`, `opted_out`)
- Messaging provider configuration: **Meta WhatsApp Cloud API** (primary provider) and **WhatsApp simulator** (fallback/demo provider when explicitly in simulator mode or when Meta credentials are unavailable; never automatically switched to after a real Meta API delivery failure)

## Output

At most six unique recommended SKUs (`0..6`), each with product display data and preserved recommendation provenance (`recommended_product_code`, `recommended_product_name`, `source_product_code`, `source_product_name`, `original_mapping_rank`, `selection_sequence`, `selection_phase`: `INITIAL_ALLOCATION` or `BACKFILL`). Also return a top-level result status (`SUCCESS`, `PARTIAL_SUCCESS`, `NO_ELIGIBLE_RECOMMENDATIONS`, or `NO_MAPPED_SOURCE_PRODUCTS`), `unfilled_slots` (`0..6`), and a structured, JSON-serializable decision trace (`DecisionTrace`) containing order-level summary, source-level priority & quota evaluations, and candidate-level eligibility & allocation evaluations.

### Separation of Eligibility Evaluation vs. Allocation Scanning in the Decision Trace

The engine explicitly separates **pre-prioritization eligibility evaluation** from **cursor-based allocation scanning** so no candidate is double-counted or misclassified:

1. **Eligibility Evaluation (Step 4 — before source prioritization)**:
   - Every mapped candidate (`1..mapping_count`) for every mapped source is evaluated against the purchased SKU set and explicit catalogue metadata (`is_rx`, `is_active`, `available_quantity`).
   - Recorded on each `CandidateTraceEntry` via `is_eligible: bool` and `eligibility_rejection_reasons: list[CandidateExclusionReason]` (`ALREADY_PURCHASED`, `CATALOGUE_METADATA_MISSING`, `RX_PRODUCT_EXCLUDED`, `PRODUCT_INACTIVE`, `PRODUCT_UNAVAILABLE`).
   - Recorded on each `SourceTraceEntry` via `mapping_count`, `eligibility_evaluations_count`, `eligible_candidate_count`, and `rejected_candidate_count`, satisfying:
     $$\text{mapping\_count} = \text{eligibility\_evaluations\_count} = \text{eligible\_candidate\_count} + \text{rejected\_candidate\_count}$$

2. **Allocation Scanning (Steps 7 & 8 — `INITIAL_ALLOCATION` and `BACKFILL`)**:
   - During slot allocation, each source advances a cursor (`0..mapping_count - 1`) only until its quota is met or all 6 global slots are filled.
   - Each `CandidateTraceEntry` records `examined_during_allocation: bool` and `allocation_decision: CandidateAllocationDecision`:
     - `SELECTED_INITIAL_ALLOCATION`: Eligible, non-duplicate candidate selected during Phase 1 (`INITIAL_ALLOCATION`).
     - `SELECTED_BACKFILL`: Eligible, non-duplicate candidate selected during Phase 2 (`BACKFILL`).
     - `SKIPPED_INELIGIBLE`: Reached by the allocation cursor and skipped because `is_eligible == False`.
     - `SKIPPED_DUPLICATE`: Reached by the allocation cursor (`is_eligible == True`), skipped because the target SKU was already selected from an earlier source (`excluded_as_duplicate == True`, `DUPLICATE_RECOMMENDATION`).
     - `NOT_REACHED`: Evaluated for eligibility in Step 4, but never visited by the allocation cursor (`examined_during_allocation == False`) because the source's quota or the 6 global recommendation slots were already filled.
   - Recorded on each `SourceTraceEntry` via `allocation_candidates_examined`, `initial_allocation_selected_count`, `backfill_selected_count`, `candidates_selected`, `duplicate_candidate_count`, and `unvisited_candidate_count`, satisfying:
     $$\text{mapping\_count} = \text{allocation\_candidates\_examined} + \text{unvisited\_candidate\_count}$$
     $$\text{candidates\_selected} = \text{initial\_allocation\_selected\_count} + \text{backfill\_selected\_count}$$
     $$\text{eligible\_candidate\_count} = \text{candidates\_selected} + \text{duplicate\_candidate\_count} + \text{unvisited\_eligible\_count}$$

## Processing sequence

1. **Validate event (workflow gate)**: accept only a trusted `ORDER_DELIVERED` event and verify the authoritative order status is `DELIVERED`. Otherwise stop without computing recommendations or sending a message (`NOT_DELIVERED`).
2. **Normalize order lines (quantity semantics)**: validate SKU codes and positive quantities; aggregate repeated SKU codes into one distinct source SKU while retaining `quantity_sum` (sum of quantities across repeated lines, where omitted quantity defaults to `1`) and `first_line_index` (earliest order-line index). **Important quantity semantics**: `quantity_sum` is used **only** as a tie-breaker for source prioritization when **more than six** mapped source SKUs exist (`M > 6`). For `M <= 6`, `quantity_sum` is not used in `priority_key`, ensuring quantity does not influence remainder allocation when all mapped sources can participate. It does **NOT** make MVP slot allocation quantity-weighted. Equal allocation remains the MVP rule.
3. **Resolve mapped sources**: retain only distinct ordered SKUs present in the mapping table (`M` mapped sources). Record unmapped ordered SKUs in the source trace (`NO_MAPPING_FOUND`). If `M == 0`, return `NO_MAPPED_SOURCE_PRODUCTS` (`unfilled_slots = 6`) and do not send a WhatsApp message.
4. **Build exclusion set & consistent candidate eligibility evaluation across all mapped sources**:
   - Build the purchased exclusion set containing **all SKUs present in the current order** (both mapped and unmapped).
   - **All mapped source products must undergo consistent eligibility evaluation before prioritization.** For each of the `M` mapped sources, evaluate all 6 of its ranked target candidates (`rank` 1..6) against the purchased exclusion set and catalogue metadata, recording **all** applicable rejection reasons in deterministic order:
     - Target in current order → excluded (`ALREADY_PURCHASED`)
     - Missing catalogue record or `is_rx` / `is_active` / `available_quantity` is `null`/unknown → excluded (`CATALOGUE_METADATA_MISSING`, fail closed)
     - `is_rx is True` → excluded (`RX_PRODUCT_EXCLUDED`)
     - `is_active is False` → excluded (`PRODUCT_INACTIVE`)
     - `available_quantity <= 0` → excluded (`PRODUCT_UNAVAILABLE`)
     - Otherwise (`is_rx is False`, `is_active is True`, `available_quantity > 0`, and not purchased) → eligible candidate (retaining its `source_product_code` and `original_mapping_rank`).
   - Count `eligible_candidate_count` (0..6) for each mapped source SKU.
5. **Deterministic and explainable source prioritization (conditional quantity tie-breaking)**:
   - After all `M` mapped source products have undergone consistent eligibility evaluation, rank them using the conditional deterministic priority key based on `M`:
   - **For `M <= 6` mapped source SKUs**:
     ```python
     priority_key = (
         -eligible_candidate_count,
         first_line_index,
         product_code,
     )
     ```
     (`eligible_candidate_count` descending, `first_line_index` ascending, `product_code` ascending)
   - **For `M > 6` mapped source SKUs**:
     ```python
     priority_key = (
         -eligible_candidate_count,
         -quantity_sum,
         first_line_index,
         product_code,
     )
     ```
     (`eligible_candidate_count` descending, `quantity_sum` descending, `first_line_index` ascending, `product_code` ascending)
   - Why this conditional priority order is explainable and deterministic:
     1. **`eligible_candidate_count` descending**: prioritizes sources that actually have valid, in-stock, non-Rx, non-purchased recommendations to offer, avoiding wasting initial quota slots on depleted sources or arbitrary early order lines.
     2. **Conditional `quantity_sum` descending (only when `M > 6`)**: ensures quantity does **not** influence remainder slot allocation when all mapped sources can participate (`M <= 6`), while providing a deterministic tie-breaker when selecting which 6 sources receive initial slots among `M > 6` mapped sources.
     3. **`first_line_index` ascending**: preserves stable order-line sequence when earlier priority criteria are equal.
     4. **`product_code` ascending**: final canonical lexical tie-breaker guaranteeing total determinism.
6. **Equal allocation of six slots across prioritized sources (no arbitrary first-six truncation)**:
   - Compute equal allocation across the `M` prioritized mapped sources:
     - `base_quota = 6 // M`
     - `remainder = 6 % M`
   - In deterministic source priority order (`index = 0 .. M-1`), assign each source an initial quota:
     $$\text{initial\_quota}(s_{\text{index}}) = \text{base\_quota} + (1 \text{ if } \text{index} < \text{remainder} \text{ else } 0)$$
   - Examples:
     - `M = 1`: `[6]`
     - `M = 2`: `[3, 3]`
     - `M = 3`: `[2, 2, 2]`
     - `M = 4`: `[2, 2, 1, 1]`
     - `M = 5`: `[2, 1, 1, 1, 1]`
     - `M = 6`: `[1, 1, 1, 1, 1, 1]`
     - `M > 6` (e.g., `M = 7` or `8`): `base_quota = 0, remainder = 6`. **No arbitrary first-six truncation by raw order line is performed.** Instead, all `M` mapped sources undergo consistent eligibility evaluation first, the top 6 sources according to the deterministic priority ordering receive an `initial_quota` of `1` each (`[1, 1, 1, 1, 1, 1, 0, ...]`), and **all `M` mapped sources** remain active for round-robin backfill in priority order.
7. **Phase 1 — Initial quota allocation (`INITIAL_ALLOCATION`)**:
   - Traverse mapped sources in deterministic source priority order.
   - For each source with `initial_quota > 0`, scan its mapped targets by ascending `original_mapping_rank` (`1..6`).
   - Accept eligible targets not yet in the global accepted set until the source's `initial_quota` is met or its 6 ranked candidates are exhausted.
   - If a candidate was already accepted from an earlier source, skip it (`DUPLICATE_RECOMMENDATION`, recording `duplicate_retained_from_source`, `duplicate_retained_rank`, and `duplicate_retained_sequence` in the trace) and continue scanning that source's next rank so the source can still fill its allocated quota.
   - Preserve recommendation provenance (`source_product_code`, `source_product_name`, `original_mapping_rank`, `selection_sequence`, `selection_phase = "INITIAL_ALLOCATION"`) in the decision trace.
8. **Phase 2 — Round-robin backfill (`BACKFILL`)**:
   - If total accepted recommendations after Phase 1 is `< 6`, perform round-robin backfill passes across **all `M` mapped sources** in deterministic source priority order (starting from priority index `0` on each pass).
   - In each pass, each source advances through its remaining unconsumed ranked candidates (`1..6`) until it contributes **one** additional eligible, globally unique target (or has no remaining candidates).
   - Repeat passes until `6` unique recommendations are selected or all `M` mapped sources have exhausted their 6 ranked candidates.
   - Preserve recommendation provenance (`source_product_code`, `source_product_name`, `original_mapping_rank`, `selection_sequence`, `selection_phase = "BACKFILL"`) in the decision trace.
9. **Return fewer than six when exhausted (no global bestseller padding in MVP)**:
   - Return `0..6` recommendations (`unfilled_slots = 6 - len(recommendations)`).
   - Result status resolution:
     - `SUCCESS`: `6` recommendations selected (`unfilled_slots = 0`).
     - `PARTIAL_SUCCESS`: `1..5` recommendations selected (`unfilled_slots = 1..5`).
     - `NO_ELIGIBLE_RECOMMENDATIONS`: `0` recommendations selected when `M >= 1` mapped sources exist (`unfilled_slots = 6`).
     - `NO_MAPPED_SOURCE_PRODUCTS`: `0` recommendations selected because `M == 0` (`unfilled_slots = 6`).
   - Do **not** relax Rx, stock, active, purchased-item, or deduplication rules to reach six.
   - **Global bestseller fallback is a future enhancement only** and is never invoked in the MVP.
10. **Dispatch gate & WhatsApp provider failure/delivery semantics**:
    - Send only if `len(recommendations) >= 1`, customer has valid messaging consent (`has_consent == true` and `opted_out == false`), and `(order_id, campaign_id)` has not already been dispatched (`ALREADY_DISPATCHED`).
    - **Provider selection & failure rules**:
      - **Meta WhatsApp Cloud API** is the **primary provider**; the **WhatsApp simulator** is the **fallback/demo provider**.
      - **No automatic failover to simulator on live Meta API failure**: Do **not** automatically switch to the simulator after a real Meta API delivery failure, because doing so would create a false impression that the customer received a message.
      - **Explicit simulator mode or unavailable credentials**: The simulator can be selected when explicitly configured in simulator/demo mode or when Meta Cloud API credentials are unavailable.
      - **Meta API transient failure**: Retry safely (respecting idempotency) and record the failure or pending retry state (`FAILED_RETRYABLE` / `PENDING_RETRY`).
      - **Meta API acceptance**: When the Meta Cloud API HTTP call succeeds, record **provider acceptance** (`PROVIDER_ACCEPTED` with `wamid`), **not** confirmed customer delivery.
      - **Actual delivery confirmation**: Transition the dispatch state to confirmed delivery (`DELIVERED`) only when a **Meta webhook status update** (`status = "delivered"`) is received.

## Determinism

Given identical order lines, mapping data, catalogue metadata, consent state, and event, the source priority ordering, selected recommendation list, provenance (`source_product_code`, original `mapping_rank`), and exclusion trace are 100% identical. Every tie is broken deterministically by:
1. Conditional source priority ordering:
   - For `M <= 6`: `(-eligible_candidate_count, first_line_index, product_code)` (`eligible_candidate_count` descending, `first_line_index` ascending, `product_code` ascending)
   - For `M > 6`: `(-eligible_candidate_count, -quantity_sum, first_line_index, product_code)` (`eligible_candidate_count` descending, `quantity_sum` descending, `first_line_index` ascending, `product_code` ascending)
2. Candidate mapping rank: ascending `1..6`

## Example 1: two ordered SKUs in the same isolated mapping cluster (fewer than six returned)

Order contains `SAMPLE-001` and `SAMPLE-002` (`M = 2 <= 6`). Both belong to **isolated mapping cluster 1** (`SAMPLE-001`..`SAMPLE-007`; note that the workbook establishes recommendation graph structure, not actual product category membership) and are excluded as already purchased.

- `SAMPLE-001` maps to `002, 003, 004, 005, 006, 007` (5 eligible after excluding `002`).
- `SAMPLE-002` maps to `003, 004, 005, 006, 007, 001` (5 eligible after excluding `001`).
- Both undergo consistent eligibility evaluation before prioritization and have `eligible_candidate_count = 5`; using the `M <= 6` key `(-eligible_candidate_count, first_line_index, product_code)`, `SAMPLE-001` comes first via `first_line_index = 0`.
- Equal allocation gives `3` initial slots to `SAMPLE-001` (picks `003` [rank 2], `004` [rank 3], `005` [rank 4]) and `3` initial slots to `SAMPLE-002` (skips duplicates `003, 004, 005`, picks `006` [rank 4], `007` [rank 5]).
- All remaining candidates across both sources are exhausted. The union of eligible mapped target codes is `{003, 004, 005, 006, 007}`.
- The algorithm returns **5** recommendations (fewer than six), preserving `source_product_code` and original `mapping_rank` for each, and does not invent or pad a 6th product.

## Example 2: more than six mapped SKUs (`M = 7`, no arbitrary first-six truncation, `quantity_sum` tie-break)

Order contains 7 mapped SKUs `L1..L7`, where `L1` (line 0, qty 1) has `0` eligible candidates (all its mapped targets are out of stock or Rx), `L2..L6` (lines 1..5, qty 1) each have `6` eligible candidates, and `L7` (line 6, qty 3) also has `6` eligible candidates.

- All 7 mapped sources first undergo consistent eligibility evaluation.
- Under the old arbitrary first-six truncation rule, `L1..L6` got the 6 initial slots and `L7` was truncated out of the initial pass despite `L1` having zero eligible candidates and `L7` having higher ordered quantity.
- Under **deterministic source prioritization** (`eligible_candidate_count` desc → `quantity_sum` desc → `first_line_index` asc → `product_code` asc), `L7` (`eligible_candidate_count = 6, quantity_sum = 3`) sorts first, followed by `L2..L6` (`eligible_candidate_count = 6, quantity_sum = 1`), and finally `L1` (`eligible_candidate_count = 0`).
- The top 6 prioritized sources (`L7, L2, L3, L4, L5, L6`) each receive `1` equal initial slot (`[1, 1, 1, 1, 1, 1, 0]`)—note that `L7`'s `quantity_sum = 3` only broke the priority tie and did **not** give `L7` 3 slots—and all 7 sources participate in round-robin backfill in priority order if needed.

## Failure/empty outcomes & canonical rejection reason codes

### Result statuses (`RecommendationResultStatus`)
- `SUCCESS`: 6 eligible, unique recommendations selected.
- `PARTIAL_SUCCESS`: 1–5 eligible, unique recommendations selected after candidate exhaustion.
- `NO_ELIGIBLE_RECOMMENDATIONS`: Mapped sources exist (`M >= 1`), but all candidate targets were excluded/ineligible; no recommendation message.
- `NO_MAPPED_SOURCE_PRODUCTS`: No ordered SKU has mappings in the mapping table (`M == 0`); no recommendation message.

### Candidate & source exclusion codes (`CandidateExclusionReason`)
- `ALREADY_PURCHASED`: Candidate SKU is present in the customer's current order.
- `CATALOGUE_METADATA_MISSING`: Fail-closed because catalogue row is missing or `is_rx`, `is_active`, or `available_quantity` is `None`.
- `RX_PRODUCT_EXCLUDED`: Candidate has `is_rx = True`.
- `PRODUCT_INACTIVE`: Candidate has `is_active = False`.
- `PRODUCT_UNAVAILABLE`: Candidate has `available_quantity <= 0`.
- `DUPLICATE_RECOMMENDATION`: Candidate was already selected from an earlier source or rank in the same order evaluation.
- `NO_MAPPING_FOUND`: Ordered source SKU has no recommendation mappings.

### Workflow & dispatch gate outcomes (Stage 5)
- `NOT_DELIVERED`: no recommendation computation/dispatch.
- `CUSTOMER_OPTED_OUT` / `NO_CONSENT`: suppress dispatch.
- `ALREADY_DISPATCHED`: idempotent no-op.
