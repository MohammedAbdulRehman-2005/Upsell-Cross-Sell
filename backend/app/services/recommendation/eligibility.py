"""Deterministic candidate eligibility evaluation (Rule 2 and Rule 4)."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.recommendation.models import (
    CandidateExclusionReason,
    CatalogueItemInput,
)


@dataclass(frozen=True)
class EligibilityCheckResult:
    """Result of evaluating a single candidate against purchased-set and catalogue rules."""

    is_eligible: bool
    excluded_as_purchased: bool
    rejection_reasons: tuple[CandidateExclusionReason, ...]
    resolved_product_name: str


def evaluate_candidate_eligibility(
    *,
    candidate_code: str,
    fallback_candidate_name: str | None,
    purchased_sku_set: set[str],
    catalogue_by_code: dict[str, CatalogueItemInput],
) -> EligibilityCheckResult:
    """Evaluate a candidate SKU against purchased-product and catalogue eligibility rules.

    Deterministic evaluation order (preserving all applicable reasons):
    1. `ALREADY_PURCHASED` if `candidate_code` is in `purchased_sku_set`.
    2. `CATALOGUE_METADATA_MISSING` if `candidate_code` is missing from `catalogue_by_code`
       or any of `is_rx`, `is_active`, `available_quantity` is `None`.
    3. `RX_PRODUCT_EXCLUDED` if `is_rx is True`.
    4. `PRODUCT_INACTIVE` if `is_active is False`.
    5. `PRODUCT_UNAVAILABLE` if `available_quantity is not None and available_quantity <= 0`.

    Never infers Rx, active, or stock values from product names and never treats
    `None` as `False`, `True`, `0`, or available.
    """
    reasons: list[CandidateExclusionReason] = []
    excluded_as_purchased = candidate_code in purchased_sku_set
    if excluded_as_purchased:
        reasons.append(CandidateExclusionReason.ALREADY_PURCHASED)

    catalogue_item = catalogue_by_code.get(candidate_code)
    resolved_name = (
        catalogue_item.product_name
        if catalogue_item is not None and catalogue_item.product_name
        else (fallback_candidate_name or candidate_code)
    )

    if catalogue_item is None:
        reasons.append(CandidateExclusionReason.CATALOGUE_METADATA_MISSING)
    else:
        missing_required_metadata = (
            catalogue_item.is_rx is None
            or catalogue_item.is_active is None
            or catalogue_item.available_quantity is None
        )
        if missing_required_metadata:
            reasons.append(CandidateExclusionReason.CATALOGUE_METADATA_MISSING)

        if catalogue_item.is_rx is True:
            reasons.append(CandidateExclusionReason.RX_PRODUCT_EXCLUDED)

        if catalogue_item.is_active is False:
            reasons.append(CandidateExclusionReason.PRODUCT_INACTIVE)

        if (
            catalogue_item.available_quantity is not None
            and catalogue_item.available_quantity <= 0
        ):
            reasons.append(CandidateExclusionReason.PRODUCT_UNAVAILABLE)

    return EligibilityCheckResult(
        is_eligible=len(reasons) == 0,
        excluded_as_purchased=excluded_as_purchased,
        rejection_reasons=tuple(reasons),
        resolved_product_name=resolved_name,
    )
