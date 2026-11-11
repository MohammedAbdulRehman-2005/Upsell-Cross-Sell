"""Tests for cross-source deduplication during initial allocation and backfill (Scenarios 25, 26, 38)."""
from __future__ import annotations

from app.services.recommendation import (
    CandidateExclusionReason,
    OrderInput,
    OrderLineInput,
    RecommendationResultStatus,
    SelectionPhase,
    recommendation_engine,
)
from tests.recommendation import make_catalogue_item, make_six_mappings


def test_scenario_25_global_duplicate_during_initial_allocation() -> None:
    """Scenario 25: Two sources recommend the same SKU during initial allocation; first retains provenance, second skips."""
    catalogue = {
        "SRC-A": make_catalogue_item("SRC-A"),
        "SRC-B": make_catalogue_item("SRC-B"),
        "SHARED-1": make_catalogue_item("SHARED-1"),
        "SHARED-2": make_catalogue_item("SHARED-2"),
    }
    for code in ["A-3", "A-4", "A-5", "A-6", "B-3", "B-4", "B-5", "B-6"]:
        catalogue[code] = make_catalogue_item(code)

    mappings = [
        # SRC-A: ranks 1..2 are SHARED-1, SHARED-2, rank 3 is A-3
        *make_six_mappings("SRC-A", ["SHARED-1", "SHARED-2", "A-3", "A-4", "A-5", "A-6"]),
        # SRC-B: ranks 1..2 are also SHARED-1, SHARED-2, ranks 3..5 are B-3, B-4, B-5
        *make_six_mappings("SRC-B", ["SHARED-1", "SHARED-2", "B-3", "B-4", "B-5", "B-6"]),
    ]

    order = OrderInput(
        order_id="ORD-S25",
        line_items=[
            OrderLineInput("SRC-A", line_index=0),
            OrderLineInput("SRC-B", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result.status == RecommendationResultStatus.SUCCESS
    selected_codes = [r.recommended_product_code for r in result.recommendations]
    # SRC-A gets quota 3 -> SHARED-1, SHARED-2, A-3
    # SRC-B gets quota 3 -> skips SHARED-1, SHARED-2, picks B-3, B-4, B-5
    assert selected_codes == ["SHARED-1", "SHARED-2", "A-3", "B-3", "B-4", "B-5"]
    assert len(set(selected_codes)) == 6

    # Verify first occurrence retains SRC-A provenance
    assert result.recommendations[0].source_product_code == "SRC-A"
    assert result.recommendations[0].original_mapping_rank == 1
    assert result.recommendations[1].source_product_code == "SRC-A"
    assert result.recommendations[1].original_mapping_rank == 2

    # Verify SRC-B's selected items start at original_mapping_rank 3
    assert result.recommendations[3].source_product_code == "SRC-B"
    assert result.recommendations[3].original_mapping_rank == 3


def test_scenario_26_duplicate_during_backfill() -> None:
    """Scenario 26: Duplicate encountered during round-robin backfill is skipped deterministically."""
    catalogue = {
        "SRC-A": make_catalogue_item("SRC-A"),
        "SRC-B": make_catalogue_item("SRC-B"),
        "A-1": make_catalogue_item("A-1"),
        "A-2": make_catalogue_item("A-2"),
        "B-1": make_catalogue_item("B-1"),
        "B-2": make_catalogue_item("B-2"),
        "B-3": make_catalogue_item("B-3"),
        "B-4": make_catalogue_item("B-4"),
        "B-5": make_catalogue_item("B-5"),
        # Make A-3..A-6 inactive so SRC-A only has 2 eligible items (shortfall of 1 on quota 3)
        "A-3": make_catalogue_item("A-3", is_active=False),
        "A-4": make_catalogue_item("A-4", is_active=False),
        "A-5": make_catalogue_item("A-5", is_active=False),
        "A-6": make_catalogue_item("A-6", is_active=False),
    }

    # Note: SRC-B has 6 eligible items: B-1, B-2, B-3, A-1 (duplicate of SRC-A's rank 1!), B-4, B-5
    # Wait: SRC-B has 6 eligible items, SRC-A has 2 eligible items -> priority order puts SRC-B first!
    # So let's inspect:
    # SRC-B (pos 1, quota 3): selects B-1 (rank 1), B-2 (rank 2), B-3 (rank 3) in INITIAL_ALLOCATION.
    # SRC-A (pos 2, quota 3): selects A-1 (rank 1), A-2 (rank 2) in INITIAL_ALLOCATION (shortfall 1).
    # Backfill pass 1 starts at SRC-B (rank 4 is A-1, which was already selected by SRC-A in INITIAL_ALLOCATION!).
    # So SRC-B skips rank 4 (A-1) as DUPLICATE_RECOMMENDATION during BACKFILL, and selects rank 5 (B-4) in BACKFILL!
    mappings = [
        *make_six_mappings("SRC-A", ["A-1", "A-2", "A-3", "A-4", "A-5", "A-6"]),
        *make_six_mappings("SRC-B", ["B-1", "B-2", "B-3", "A-1", "B-4", "B-5"]),
    ]

    order = OrderInput(
        order_id="ORD-S26",
        line_items=[
            OrderLineInput("SRC-A", line_index=0),
            OrderLineInput("SRC-B", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert result.status == RecommendationResultStatus.SUCCESS
    assert [r.recommended_product_code for r in result.recommendations] == [
        "B-1",
        "B-2",
        "B-3",
        "A-1",
        "A-2",
        "B-4",
    ]
    backfill_rec = result.recommendations[5]
    assert backfill_rec.recommended_product_code == "B-4"
    assert backfill_rec.source_product_code == "SRC-B"
    assert backfill_rec.original_mapping_rank == 5
    assert backfill_rec.selection_phase == SelectionPhase.BACKFILL


def test_scenario_38_duplicate_rejection_recorded_in_trace() -> None:
    """Scenario 38: Trace records DUPLICATE_RECOMMENDATION and exact retained source/rank/sequence."""
    catalogue = {
        "SRC-A": make_catalogue_item("SRC-A"),
        "SRC-B": make_catalogue_item("SRC-B"),
        "DUP-SKU": make_catalogue_item("DUP-SKU"),
    }
    for code in ["A-2", "A-3", "A-4", "A-5", "A-6", "B-2", "B-3", "B-4", "B-5", "B-6"]:
        catalogue[code] = make_catalogue_item(code)

    mappings = [
        *make_six_mappings("SRC-A", ["DUP-SKU", "A-2", "A-3", "A-4", "A-5", "A-6"]),
        *make_six_mappings("SRC-B", ["DUP-SKU", "B-2", "B-3", "B-4", "B-5", "B-6"]),
    ]

    order = OrderInput(
        order_id="ORD-S38",
        line_items=[
            OrderLineInput("SRC-A", line_index=0),
            OrderLineInput("SRC-B", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    dup_entries = [
        c
        for c in result.trace.candidate_evaluations
        if c.source_product_code == "SRC-B" and c.candidate_product_code == "DUP-SKU"
    ]
    assert len(dup_entries) == 1
    dup_entry = dup_entries[0]
    assert dup_entry.is_eligible is True
    assert dup_entry.excluded_as_duplicate is True
    assert CandidateExclusionReason.DUPLICATE_RECOMMENDATION in dup_entry.rejection_reasons
    assert dup_entry.duplicate_retained_from_source == "SRC-A"
    assert dup_entry.duplicate_retained_rank == 1
    assert dup_entry.duplicate_retained_sequence == 1
    assert dup_entry.selected is False
