"""Stage 4 tests for source prioritization, quota exhaustion, tie-breakers, and backfill (Scenarios 23, 24, 27..33)."""
from __future__ import annotations

from app.services.recommendation import (
    OrderInput,
    OrderLineInput,
    RecommendationResultStatus,
    SelectionPhase,
    recommendation_engine,
)
from tests.recommendation import (
    build_independent_clusters_fixture,
    make_catalogue_item,
    make_six_mappings,
)


def test_source_with_fewer_eligible_candidates_backfilled_from_other_source() -> None:
    """Scenarios 23 & 24:
    Two mapped sources (quota 3 each).
    - SRC-01 has 6 eligible candidates (`eligible_candidate_count = 6`).
    - SRC-02 has only 1 eligible candidate (`eligible_candidate_count = 1`; ranks 2..6 are out of stock).
    - Initial allocation: SRC-01 fills 3 (T1..T3); SRC-02 fills 1 (T1), leaving 2 slots unfilled.
    - Backfill: SRC-01 fills the remaining 2 slots (T4, T5) during `BACKFILL`.
    """
    catalogue, mappings = build_independent_clusters_fixture(num_sources=2)
    for rank in range(2, 7):
        code = f"SRC-02-T{rank}"
        catalogue[code] = make_catalogue_item(code, available_quantity=0)

    order = OrderInput(
        order_id="ORD-BACKFILL-23-24",
        line_items=[
            OrderLineInput(product_code="SRC-01", quantity=1, line_index=0),
            OrderLineInput(product_code="SRC-02", quantity=1, line_index=1),
        ],
    )

    result = recommendation_engine.generate(
        order=order,
        mappings=mappings,
        catalogue=catalogue,
    )

    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    assert [(r.recommended_product_code, r.selection_phase) for r in result.recommendations] == [
        ("SRC-01-T1", SelectionPhase.INITIAL_ALLOCATION),
        ("SRC-01-T2", SelectionPhase.INITIAL_ALLOCATION),
        ("SRC-01-T3", SelectionPhase.INITIAL_ALLOCATION),
        ("SRC-02-T1", SelectionPhase.INITIAL_ALLOCATION),
        ("SRC-01-T4", SelectionPhase.BACKFILL),
        ("SRC-01-T5", SelectionPhase.BACKFILL),
    ]


def test_m_greater_than_6_initial_top_six_and_non_initial_source_participates_in_backfill() -> None:
    """Scenarios 27 & 28:
    Order has 7 mapped sources (`M = 7`).
    - Sources 1..6 each have only 1 eligible candidate (`eligible_candidate_count = 1`), except
      SRC-06 whose single eligible candidate is a duplicate of SRC-01-T1!
      Wait: to make SRC-01..06 rank ahead of SRC-07 initially, give SRC-01..06 `quantity_sum = 2`
      and SRC-07 `quantity_sum = 1`, all with `eligible_candidate_count = 1`.
    - Make SRC-06's 1 eligible candidate be `SRC-01-T1` (global duplicate of SRC-01's pick).
    - During `INITIAL_ALLOCATION`:
      - Top 6 prioritized sources are `SRC-01..SRC-06` (each gets `initial_quota = 1`); `SRC-07` gets `0`.
      - `SRC-01..SRC-05` each contribute 1 unique item (5 total).
      - `SRC-06`'s only eligible candidate (`SRC-01-T1`) is skipped as a global duplicate, so 1 slot remains open.
    - During `BACKFILL`:
      - Round-robin scans `SRC-01..SRC-07` in priority order; `SRC-01..SRC-06` are exhausted, so `SRC-07`
        (which had `initial_quota = 0`) contributes `SRC-07-T1` to fill the 6th slot!
    """
    catalogue, _ = build_independent_clusters_fixture(num_sources=7)
    mappings = []

    for i in range(1, 8):
        src_code = f"SRC-{i:02d}"
        if i == 6:
            # SRC-06 rank 1 points to SRC-01-T1 (eligible, but will be duplicate of SRC-01)
            targets = ["SRC-01-T1"] + [f"SRC-06-T{r}" for r in range(2, 7)]
        else:
            targets = [f"SRC-{i:02d}-T{r}" for r in range(1, 7)]
        mappings.extend(make_six_mappings(src_code, targets))
        # Mark ranks 2..6 out of stock so every source has eligible_candidate_count == 1
        for r in range(2, 7):
            t_code = f"SRC-{i:02d}-T{r}"
            catalogue[t_code] = make_catalogue_item(t_code, available_quantity=0)

    order = OrderInput(
        order_id="ORD-M7-BACKFILL",
        line_items=[
            OrderLineInput(product_code="SRC-01", quantity=2, line_index=0),
            OrderLineInput(product_code="SRC-02", quantity=2, line_index=1),
            OrderLineInput(product_code="SRC-03", quantity=2, line_index=2),
            OrderLineInput(product_code="SRC-04", quantity=2, line_index=3),
            OrderLineInput(product_code="SRC-05", quantity=2, line_index=4),
            OrderLineInput(product_code="SRC-06", quantity=2, line_index=5),
            OrderLineInput(product_code="SRC-07", quantity=1, line_index=6),
        ],
    )

    result = recommendation_engine.generate(
        order=order,
        mappings=mappings,
        catalogue=catalogue,
    )

    assert result.status == RecommendationResultStatus.SUCCESS
    assert len(result.recommendations) == 6
    src_eval_by_code = {s.product_code: s for s in result.trace.source_evaluations}
    assert src_eval_by_code["SRC-07"].initial_quota == 0
    assert src_eval_by_code["SRC-07"].candidates_selected == 1

    sixth_pick = result.recommendations[5]
    assert sixth_pick.recommended_product_code == "SRC-07-T1"
    assert sixth_pick.source_product_code == "SRC-07"
    assert sixth_pick.selection_phase == SelectionPhase.BACKFILL


def test_quantity_tie_break_only_when_m_greater_than_6_and_ignored_when_m_le_6() -> None:
    """Scenarios 29 & 30:
    - Scenario 29 (`M = 7 > 6`): All 7 sources have `eligible_candidate_count = 6`.
      `SRC-07` on line 6 has `quantity = 10`, while `SRC-01..SRC-06` have `quantity = 1`.
      `SRC-07` sorts first in priority order (`priority_position = 1`) and gets 1 initial slot,
      while `SRC-06` (line 5, qty 1) is 7th and gets `initial_quota = 0`.
    - Scenario 30 (`M = 4 <= 6`): All 4 sources have `eligible_candidate_count = 6`.
      `SRC-04` on line 3 has `quantity = 100`, while `SRC-01..SRC-03` have `quantity = 1`.
      Because `M <= 6`, quantity is NOT in `priority_key`; remainder slots go to `SRC-01` and `SRC-02`
      by `first_line_index` (`[2, 2, 1, 1]`), and `SRC-04` still gets only 1 slot.
    """
    catalogue, mappings = build_independent_clusters_fixture(num_sources=7)

    # Scenario 29: M = 7 > 6
    order_m7 = OrderInput(
        order_id="ORD-QTY-M7",
        line_items=[
            OrderLineInput(product_code="SRC-01", quantity=1, line_index=0),
            OrderLineInput(product_code="SRC-02", quantity=1, line_index=1),
            OrderLineInput(product_code="SRC-03", quantity=1, line_index=2),
            OrderLineInput(product_code="SRC-04", quantity=1, line_index=3),
            OrderLineInput(product_code="SRC-05", quantity=1, line_index=4),
            OrderLineInput(product_code="SRC-06", quantity=1, line_index=5),
            OrderLineInput(product_code="SRC-07", quantity=10, line_index=6),
        ],
    )
    res_m7 = recommendation_engine.generate(
        order=order_m7,
        mappings=mappings,
        catalogue=catalogue,
    )
    prioritized_codes_m7 = [s.product_code for s in res_m7.trace.source_evaluations]
    assert prioritized_codes_m7 == [
        "SRC-07",
        "SRC-01",
        "SRC-02",
        "SRC-03",
        "SRC-04",
        "SRC-05",
        "SRC-06",
    ]
    assert [s.initial_quota for s in res_m7.trace.source_evaluations] == [1, 1, 1, 1, 1, 1, 0]

    # Scenario 30: M = 4 <= 6
    order_m4 = OrderInput(
        order_id="ORD-QTY-M4",
        line_items=[
            OrderLineInput(product_code="SRC-01", quantity=1, line_index=0),
            OrderLineInput(product_code="SRC-02", quantity=1, line_index=1),
            OrderLineInput(product_code="SRC-03", quantity=1, line_index=2),
            OrderLineInput(product_code="SRC-04", quantity=100, line_index=3),
        ],
    )
    res_m4 = recommendation_engine.generate(
        order=order_m4,
        mappings=mappings,
        catalogue=catalogue,
    )
    prioritized_codes_m4 = [s.product_code for s in res_m4.trace.source_evaluations]
    assert prioritized_codes_m4 == ["SRC-01", "SRC-02", "SRC-03", "SRC-04"]
    assert [s.initial_quota for s in res_m4.trace.source_evaluations] == [2, 2, 1, 1]


def test_priority_tie_breakers_by_eligible_count_then_line_index_then_product_code() -> None:
    """Scenarios 31 & 32:
    - Equal eligible counts resolved by ascending `first_line_index`.
    - Equal `first_line_index` resolved by ascending `product_code`.
    """
    catalogue, mappings = build_independent_clusters_fixture(num_sources=4)
    # Give SRC-04 (line 1) 6 eligible candidates, and SRC-01 (line 0) only 2 eligible candidates
    for r in range(3, 7):
        catalogue[f"SRC-01-T{r}"] = make_catalogue_item(f"SRC-01-T{r}", available_quantity=0)

    order = OrderInput(
        order_id="ORD-TIE-31",
        line_items=[
            OrderLineInput(product_code="SRC-01", quantity=1, line_index=0),
            OrderLineInput(product_code="SRC-02", quantity=1, line_index=1),
            OrderLineInput(product_code="SRC-03", quantity=1, line_index=2),
            OrderLineInput(product_code="SRC-04", quantity=1, line_index=3),
        ],
    )
    res = recommendation_engine.generate(
        order=order,
        mappings=mappings,
        catalogue=catalogue,
    )
    # SRC-02, SRC-03, SRC-04 all have eligible_candidate_count=6, tied-broken by first_line_index (1, 2, 3),
    # while SRC-01 has eligible_candidate_count=2 and sorts 4th.
    assert [s.product_code for s in res.trace.source_evaluations] == [
        "SRC-02",
        "SRC-03",
        "SRC-04",
        "SRC-01",
    ]
    assert [s.initial_quota for s in res.trace.source_evaluations] == [2, 2, 1, 1]


def test_equal_first_line_index_resolved_by_product_code_in_allocator() -> None:
    """Scenario 32: Direct unit test of `allocate_initial_quotas` when two sources share
    identical `eligible_candidate_count` and `first_line_index`, verifying lexical `product_code` tie-break.
    """
    from app.services.recommendation import NormalizedOrderSource, allocate_initial_quotas

    sources = [
        NormalizedOrderSource(
            product_code="SKU-ZETA",
            product_name="Zeta",
            quantity_sum=1,
            first_line_index=0,
            occurrence_count=1,
        ),
        NormalizedOrderSource(
            product_code="SKU-ALPHA",
            product_name="Alpha",
            quantity_sum=1,
            first_line_index=0,
            occurrence_count=1,
        ),
    ]
    plans = allocate_initial_quotas(
        sources,
        eligible_counts_by_source={"SKU-ZETA": 5, "SKU-ALPHA": 5},
    )
    assert [p.product_code for p in plans] == ["SKU-ALPHA", "SKU-ZETA"]
