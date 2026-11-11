"""Stage 4 tests for order normalization and repeated line aggregation (Scenarios 16, 17, 18)."""
from __future__ import annotations

import pytest

from app.services.recommendation import (
    CandidateExclusionReason,
    OrderInput,
    OrderLineInput,
    RecommendationInputError,
    RecommendationResultStatus,
    normalize_order_lines,
    recommendation_engine,
)
from tests.recommendation import (
    build_independent_clusters_fixture,
    make_catalogue_item,
    make_six_mappings,
)


def test_duplicate_order_lines_aggregated_preserving_first_line_index() -> None:
    """Scenario 17: Repeated SKU order lines are aggregated into one distinct source SKU.
    Example from specification:
      A, quantity 2 (line 0)
      B, quantity 1 (line 1)
      A, quantity 3 (line 2)
    -> A: quantity_sum = 5, first_line_index = 0
       B: quantity_sum = 1, first_line_index = 1
       purchased_skus = {A, B}
    """
    order = OrderInput(
        order_id="ORD-AGG-01",
        line_items=[
            OrderLineInput(product_code="SKU-A", quantity=2, line_index=0),
            OrderLineInput(product_code="SKU-B", quantity=1, line_index=1),
            OrderLineInput(product_code="SKU-A", quantity=3, line_index=2),
        ],
    )
    sources, purchased = normalize_order_lines(order)

    assert len(sources) == 2
    assert purchased == {"SKU-A", "SKU-B"}

    assert sources[0].product_code == "SKU-A"
    assert sources[0].quantity_sum == 5
    assert sources[0].first_line_index == 0
    assert sources[0].occurrence_count == 2

    assert sources[1].product_code == "SKU-B"
    assert sources[1].quantity_sum == 1
    assert sources[1].first_line_index == 1
    assert sources[1].occurrence_count == 1


def test_repeated_order_lines_do_not_receive_duplicate_source_quotas() -> None:
    """Scenario 17: Repeated SKU-A on multiple lines is treated as ONE source in allocation."""
    catalogue, mappings = build_independent_clusters_fixture(num_sources=2)
    order = OrderInput(
        order_id="ORD-AGG-02",
        line_items=[
            OrderLineInput(product_code="SRC-01", quantity=2, line_index=0),
            OrderLineInput(product_code="SRC-02", quantity=1, line_index=1),
            OrderLineInput(product_code="SRC-01", quantity=4, line_index=2),
        ],
    )

    result = recommendation_engine.generate(
        order=order,
        mappings=mappings,
        catalogue=catalogue,
    )

    assert result.trace.order_summary.original_order_line_count == 3
    assert result.trace.order_summary.distinct_source_sku_count == 2
    assert result.trace.order_summary.total_quantity_per_source == {"SRC-01": 6, "SRC-02": 1}
    # Equal allocation for 2 distinct sources is 3 + 3
    assert [s.initial_quota for s in result.trace.source_evaluations] == [3, 3]
    assert [s.candidates_selected for s in result.trace.source_evaluations] == [3, 3]


def test_already_purchased_and_multiple_purchased_skus_excluded() -> None:
    """Scenarios 16 & 18: Every SKU present in the customer's order (including unmapped or other lines)
    is excluded from the recommendation list with `ALREADY_PURCHASED`.
    """
    codes = [f"SAMPLE-{i:03d}" for i in range(1, 9)]
    catalogue = {c: make_catalogue_item(c) for c in codes}
    # SAMPLE-001 maps to 002..007
    mappings = make_six_mappings(
        "SAMPLE-001",
        ["SAMPLE-002", "SAMPLE-003", "SAMPLE-004", "SAMPLE-005", "SAMPLE-006", "SAMPLE-007"],
    )

    # Customer orders SAMPLE-001 (mapped), SAMPLE-002 (unmapped here, rank 1 target), and SAMPLE-005 (rank 4 target)
    order = OrderInput(
        order_id="ORD-PURCHASED-01",
        line_items=[
            OrderLineInput(product_code="SAMPLE-001", quantity=1, line_index=0),
            OrderLineInput(product_code="SAMPLE-002", quantity=1, line_index=1),
            OrderLineInput(product_code="SAMPLE-005", quantity=2, line_index=2),
        ],
    )

    result = recommendation_engine.generate(
        order=order,
        mappings=mappings,
        catalogue=catalogue,
    )

    assert result.status == RecommendationResultStatus.PARTIAL_SUCCESS
    assert result.trace.ordered_recommended_skus == [
        "SAMPLE-003",
        "SAMPLE-004",
        "SAMPLE-006",
        "SAMPLE-007",
    ]

    by_candidate = {c.candidate_product_code: c for c in result.trace.candidate_evaluations}
    assert by_candidate["SAMPLE-002"].excluded_as_purchased is True
    assert CandidateExclusionReason.ALREADY_PURCHASED in by_candidate["SAMPLE-002"].rejection_reasons
    assert by_candidate["SAMPLE-005"].excluded_as_purchased is True
    assert CandidateExclusionReason.ALREADY_PURCHASED in by_candidate["SAMPLE-005"].rejection_reasons


def test_invalid_order_inputs_rejected() -> None:
    with pytest.raises(RecommendationInputError, match="order_id must be a non-empty string"):
        normalize_order_lines(OrderInput(order_id="  ", line_items=[]))

    with pytest.raises(RecommendationInputError, match="invalid quantity"):
        normalize_order_lines(
            OrderInput(
                order_id="ORD-BAD-QTY",
                line_items=[OrderLineInput(product_code="SAMPLE-001", quantity=0)],
            )
        )

    with pytest.raises(RecommendationInputError, match="Duplicate line_index"):
        normalize_order_lines(
            OrderInput(
                order_id="ORD-DUP-LINE",
                line_items=[
                    OrderLineInput(product_code="SAMPLE-001", quantity=1, line_index=0),
                    OrderLineInput(product_code="SAMPLE-002", quantity=1, line_index=0),
                ],
            )
        )
