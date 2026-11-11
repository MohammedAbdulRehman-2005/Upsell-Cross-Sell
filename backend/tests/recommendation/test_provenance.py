"""Tests for provenance and decision trace integrity (Scenarios 34, 35, 36, 37)."""
from __future__ import annotations

from app.schemas.recommendation import RecommendationResultSchema
from app.services.recommendation import (
    CandidateExclusionReason,
    OrderInput,
    OrderLineInput,
    SelectionPhase,
    recommendation_engine,
)
from tests.recommendation import make_catalogue_item, make_six_mappings


def test_scenario_34_original_mapping_rank_preserved_when_earlier_ranks_skipped() -> None:
    """Scenario 34: Original mapping rank (1..6) is preserved even when intermediate ranks are skipped."""
    catalogue = {
        "SRC-1": make_catalogue_item("SRC-1"),
        "T-1": make_catalogue_item("T-1", is_rx=True),
        "T-2": make_catalogue_item("T-2"),
        "T-3": make_catalogue_item("T-3", is_active=False),
        "T-4": make_catalogue_item("T-4", available_quantity=0),
        "T-5": make_catalogue_item("T-5"),
        "T-6": make_catalogue_item("T-6"),
    }
    mappings = make_six_mappings("SRC-1", ["T-1", "T-2", "T-3", "T-4", "T-5", "T-6"])

    order = OrderInput(order_id="ORD-S34", line_items=[OrderLineInput("SRC-1")])
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    assert [
        (r.recommended_product_code, r.original_mapping_rank, r.selection_sequence)
        for r in result.recommendations
    ] == [
        ("T-2", 2, 1),
        ("T-5", 5, 2),
        ("T-6", 6, 3),
    ]


def test_scenario_35_source_attribution_preserved() -> None:
    """Scenario 35: Each selected recommendation preserves exact source_product_code and source_product_name."""
    catalogue = {
        "SRC-ALPHA": make_catalogue_item("SRC-ALPHA", name="Alpha Face Wash"),
        "SRC-BETA": make_catalogue_item("SRC-BETA", name="Beta Moisturizer"),
    }
    for i in range(1, 7):
        catalogue[f"A-{i}"] = make_catalogue_item(f"A-{i}", name=f"Alpha Rec {i}")
        catalogue[f"B-{i}"] = make_catalogue_item(f"B-{i}", name=f"Beta Rec {i}")

    mappings = [
        *make_six_mappings("SRC-ALPHA", [f"A-{i}" for i in range(1, 7)]),
        *make_six_mappings("SRC-BETA", [f"B-{i}" for i in range(1, 7)]),
    ]

    order = OrderInput(
        order_id="ORD-S35",
        line_items=[
            OrderLineInput("SRC-ALPHA", line_index=0),
            OrderLineInput("SRC-BETA", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    alpha_recs = [r for r in result.recommendations if r.source_product_code == "SRC-ALPHA"]
    beta_recs = [r for r in result.recommendations if r.source_product_code == "SRC-BETA"]

    assert len(alpha_recs) == 3
    assert len(beta_recs) == 3
    assert all(r.source_product_name == "Alpha Face Wash" for r in alpha_recs)
    assert all(r.source_product_name == "Beta Moisturizer" for r in beta_recs)


def test_scenario_36_initial_allocation_vs_backfill_recorded_accurately() -> None:
    """Scenario 36: Both SelectedRecommendation and CandidateTraceEntry record INITIAL_ALLOCATION vs BACKFILL."""
    catalogue = {
        "SRC-1": make_catalogue_item("SRC-1"),
        "SRC-2": make_catalogue_item("SRC-2"),
        "S1-1": make_catalogue_item("S1-1"),
    }
    # SRC-1 only has 1 eligible candidate; S1-2..S1-6 are inactive
    for i in range(2, 7):
        catalogue[f"S1-{i}"] = make_catalogue_item(f"S1-{i}", is_active=False)
    for i in range(1, 7):
        catalogue[f"S2-{i}"] = make_catalogue_item(f"S2-{i}")

    mappings = [
        *make_six_mappings("SRC-1", [f"S1-{i}" for i in range(1, 7)]),
        *make_six_mappings("SRC-2", [f"S2-{i}" for i in range(1, 7)]),
    ]

    order = OrderInput(
        order_id="ORD-S36",
        line_items=[
            OrderLineInput("SRC-1", line_index=0),
            OrderLineInput("SRC-2", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    # SRC-2 has 6 eligible -> priority pos 1 (quota 3 -> S2-1, S2-2, S2-3 in INITIAL_ALLOCATION)
    # SRC-1 has 1 eligible -> priority pos 2 (quota 3 -> S1-1 in INITIAL_ALLOCATION, shortfall 2)
    # Backfill selects S2-4, S2-5 in BACKFILL
    phases = [(r.recommended_product_code, r.selection_phase) for r in result.recommendations]
    assert phases == [
        ("S2-1", SelectionPhase.INITIAL_ALLOCATION),
        ("S2-2", SelectionPhase.INITIAL_ALLOCATION),
        ("S2-3", SelectionPhase.INITIAL_ALLOCATION),
        ("S1-1", SelectionPhase.INITIAL_ALLOCATION),
        ("S2-4", SelectionPhase.BACKFILL),
        ("S2-5", SelectionPhase.BACKFILL),
    ]

    # Verify candidate trace matches selection_phase and selection_sequence
    selected_traces = [c for c in result.trace.candidate_evaluations if c.selected]
    assert len(selected_traces) == 6
    trace_by_code = {c.candidate_product_code: c for c in selected_traces}
    assert trace_by_code["S2-1"].selection_phase == SelectionPhase.INITIAL_ALLOCATION
    assert trace_by_code["S2-1"].selection_sequence == 1
    assert trace_by_code["S2-4"].selection_phase == SelectionPhase.BACKFILL
    assert trace_by_code["S2-4"].selection_sequence == 5
    assert trace_by_code["S2-5"].selection_phase == SelectionPhase.BACKFILL
    assert trace_by_code["S2-5"].selection_sequence == 6


def test_scenario_37_rejection_reasons_recorded_in_trace_and_pydantic_validates() -> None:
    """Scenario 37: Every candidate rejection reason is recorded in the trace and validates against Pydantic schema."""
    catalogue = {
        "SRC-1": make_catalogue_item("SRC-1"),
        "PURCHASED-SKU": make_catalogue_item("PURCHASED-SKU"),
        "RX-SKU": make_catalogue_item("RX-SKU", is_rx=True),
        "INACTIVE-SKU": make_catalogue_item("INACTIVE-SKU", is_active=False),
        "OOS-SKU": make_catalogue_item("OOS-SKU", available_quantity=0),
        "VALID-SKU": make_catalogue_item("VALID-SKU"),
        # MISSING-META-SKU omitted from catalogue
    }
    mappings = make_six_mappings(
        "SRC-1",
        [
            "PURCHASED-SKU",
            "RX-SKU",
            "INACTIVE-SKU",
            "OOS-SKU",
            "MISSING-META-SKU",
            "VALID-SKU",
        ],
    )

    order = OrderInput(
        order_id="ORD-S37",
        line_items=[
            OrderLineInput("SRC-1", line_index=0),
            OrderLineInput("PURCHASED-SKU", line_index=1),
        ],
    )
    result = recommendation_engine.generate(order=order, mappings=mappings, catalogue=catalogue)

    trace_by_code = {
        c.candidate_product_code: c
        for c in result.trace.candidate_evaluations
        if c.source_product_code == "SRC-1"
    }
    assert trace_by_code["PURCHASED-SKU"].rejection_reasons == [
        CandidateExclusionReason.ALREADY_PURCHASED
    ]
    assert trace_by_code["RX-SKU"].rejection_reasons == [
        CandidateExclusionReason.RX_PRODUCT_EXCLUDED
    ]
    assert trace_by_code["INACTIVE-SKU"].rejection_reasons == [
        CandidateExclusionReason.PRODUCT_INACTIVE
    ]
    assert trace_by_code["OOS-SKU"].rejection_reasons == [
        CandidateExclusionReason.PRODUCT_UNAVAILABLE
    ]
    assert trace_by_code["MISSING-META-SKU"].rejection_reasons == [
        CandidateExclusionReason.CATALOGUE_METADATA_MISSING
    ]
    assert trace_by_code["VALID-SKU"].rejection_reasons == []
    assert trace_by_code["VALID-SKU"].selected is True

    # Validate round-trip through Pydantic v2 schema
    schema_obj = RecommendationResultSchema.model_validate(result.to_dict())
    assert schema_obj.order_id == "ORD-S37"
    assert len(schema_obj.recommendations) == 1
    assert schema_obj.recommendations[0].recommended_product_code == "VALID-SKU"
