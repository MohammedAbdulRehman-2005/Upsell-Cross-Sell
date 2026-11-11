"""Stage 4 tests for candidate eligibility filtering (Scenarios 8..15)."""
from __future__ import annotations

import pytest

from app.services.recommendation import (
    CandidateExclusionReason,
    CatalogueItemInput,
    OrderInput,
    OrderLineInput,
    RecommendationResultStatus,
    evaluate_candidate_eligibility,
    recommendation_engine,
)
from tests.recommendation import make_catalogue_item, make_six_mappings


@pytest.mark.parametrize(
    ("override_kwargs", "expected_reason"),
    [
        # Scenario 8: Rx candidate excluded
        ({"is_rx": True, "is_active": True, "available_quantity": 10}, CandidateExclusionReason.RX_PRODUCT_EXCLUDED),
        # Scenario 9: Inactive candidate excluded
        ({"is_rx": False, "is_active": False, "available_quantity": 10}, CandidateExclusionReason.PRODUCT_INACTIVE),
        # Scenario 10: Zero-stock candidate excluded
        ({"is_rx": False, "is_active": True, "available_quantity": 0}, CandidateExclusionReason.PRODUCT_UNAVAILABLE),
        # Scenario 11: Negative-stock candidate excluded
        ({"is_rx": False, "is_active": True, "available_quantity": -5}, CandidateExclusionReason.PRODUCT_UNAVAILABLE),
        # Scenario 12: Unknown Rx status rejected (fail closed)
        ({"is_rx": None, "is_active": True, "available_quantity": 10}, CandidateExclusionReason.CATALOGUE_METADATA_MISSING),
        # Scenario 13: Unknown active status rejected (fail closed)
        ({"is_rx": False, "is_active": None, "available_quantity": 10}, CandidateExclusionReason.CATALOGUE_METADATA_MISSING),
        # Scenario 14: Unknown stock rejected (fail closed)
        ({"is_rx": False, "is_active": True, "available_quantity": None}, CandidateExclusionReason.CATALOGUE_METADATA_MISSING),
    ],
)
def test_individual_eligibility_exclusion_rules_8_to_14(
    override_kwargs: dict[str, object],
    expected_reason: CandidateExclusionReason,
) -> None:
    target_codes = [f"TGT-{i:02d}" for i in range(1, 7)]
    catalogue: dict[str, CatalogueItemInput] = {
        "SRC-01": make_catalogue_item("SRC-01"),
    }
    for code in target_codes:
        catalogue[code] = make_catalogue_item(code)

    # Apply the specific ineligible condition to TGT-01 (rank 1)
    catalogue["TGT-01"] = make_catalogue_item("TGT-01", **override_kwargs)  # type: ignore[arg-type]
    mappings = make_six_mappings("SRC-01", target_codes)
    order = OrderInput(
        order_id="ORD-ELIG-SINGLE",
        line_items=[OrderLineInput(product_code="SRC-01", quantity=1, line_index=0)],
    )

    result = recommendation_engine.generate(
        order=order,
        mappings=mappings,
        catalogue=catalogue,
    )

    assert result.status == RecommendationResultStatus.PARTIAL_SUCCESS
    assert result.trace.ordered_recommended_skus == [
        "TGT-02",
        "TGT-03",
        "TGT-04",
        "TGT-05",
        "TGT-06",
    ]
    tgt_01_trace = result.trace.candidate_evaluations[0]
    assert tgt_01_trace.candidate_product_code == "TGT-01"
    assert tgt_01_trace.is_eligible is False
    assert tgt_01_trace.selected is False
    assert tgt_01_trace.rejection_reasons == [expected_reason]


def test_multiple_exclusion_conditions_recorded_together_in_deterministic_order() -> None:
    """Scenario 15: When a candidate has multiple exclusion conditions, preserve ALL applicable
    reasons in deterministic order rather than hiding other failures.
    """
    catalogue = {
        "TGT-MULTI": CatalogueItemInput(
            product_code="TGT-MULTI",
            product_name="Multi-Fail Candidate",
            is_rx=True,
            is_active=False,
            available_quantity=0,
        ),
        "TGT-UNKNOWN-AND-RX": CatalogueItemInput(
            product_code="TGT-UNKNOWN-AND-RX",
            product_name="Unknown Stock + Rx + Inactive",
            is_rx=True,
            is_active=False,
            available_quantity=None,
        ),
    }

    # Purchased + Rx + Inactive + Unavailable
    check_1 = evaluate_candidate_eligibility(
        candidate_code="TGT-MULTI",
        fallback_candidate_name=None,
        purchased_sku_set={"TGT-MULTI"},
        catalogue_by_code=catalogue,
    )
    assert check_1.is_eligible is False
    assert check_1.excluded_as_purchased is True
    assert list(check_1.rejection_reasons) == [
        CandidateExclusionReason.ALREADY_PURCHASED,
        CandidateExclusionReason.RX_PRODUCT_EXCLUDED,
        CandidateExclusionReason.PRODUCT_INACTIVE,
        CandidateExclusionReason.PRODUCT_UNAVAILABLE,
    ]

    # Purchased + Unknown Stock + Rx + Inactive
    check_2 = evaluate_candidate_eligibility(
        candidate_code="TGT-UNKNOWN-AND-RX",
        fallback_candidate_name=None,
        purchased_sku_set={"TGT-UNKNOWN-AND-RX"},
        catalogue_by_code=catalogue,
    )
    assert check_2.is_eligible is False
    assert list(check_2.rejection_reasons) == [
        CandidateExclusionReason.ALREADY_PURCHASED,
        CandidateExclusionReason.CATALOGUE_METADATA_MISSING,
        CandidateExclusionReason.RX_PRODUCT_EXCLUDED,
        CandidateExclusionReason.PRODUCT_INACTIVE,
    ]
