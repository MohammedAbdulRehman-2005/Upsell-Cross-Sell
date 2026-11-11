"""Pure domain models and enumerations for the Stage 4 deterministic recommendation engine."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

MAX_RECOMMENDATIONS: int = 6


class CandidateExclusionReason(StrEnum):
    """Deterministic exclusion reasons recorded in candidate and source traces."""

    ALREADY_PURCHASED = "ALREADY_PURCHASED"
    CATALOGUE_METADATA_MISSING = "CATALOGUE_METADATA_MISSING"
    RX_PRODUCT_EXCLUDED = "RX_PRODUCT_EXCLUDED"
    PRODUCT_INACTIVE = "PRODUCT_INACTIVE"
    PRODUCT_UNAVAILABLE = "PRODUCT_UNAVAILABLE"
    DUPLICATE_RECOMMENDATION = "DUPLICATE_RECOMMENDATION"
    NO_MAPPING_FOUND = "NO_MAPPING_FOUND"


class SelectionPhase(StrEnum):
    """Phase during which a recommendation candidate was selected."""

    INITIAL_ALLOCATION = "INITIAL_ALLOCATION"
    BACKFILL = "BACKFILL"


class CandidateAllocationDecision(StrEnum):
    """Explicit allocation-phase outcome for a candidate, distinct from pre-prioritization eligibility."""

    SELECTED_INITIAL_ALLOCATION = "SELECTED_INITIAL_ALLOCATION"
    SELECTED_BACKFILL = "SELECTED_BACKFILL"
    SKIPPED_INELIGIBLE = "SKIPPED_INELIGIBLE"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    NOT_REACHED = "NOT_REACHED"


class RecommendationResultStatus(StrEnum):
    """Deterministic outcome status for a recommendation generation run."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    NO_ELIGIBLE_RECOMMENDATIONS = "NO_ELIGIBLE_RECOMMENDATIONS"
    NO_MAPPED_SOURCE_PRODUCTS = "NO_MAPPED_SOURCE_PRODUCTS"


@dataclass(frozen=True)
class OrderLineInput:
    """Single order line item supplied to the recommendation engine."""

    product_code: str
    quantity: int = 1
    line_index: int | None = None
    product_name: str | None = None


@dataclass(frozen=True)
class OrderInput:
    """Customer order input for the pure recommendation engine."""

    order_id: str
    line_items: tuple[OrderLineInput, ...] | list[OrderLineInput]


@dataclass(frozen=True)
class NormalizedOrderSource:
    """Distinct ordered SKU after aggregating repeated order lines."""

    product_code: str
    product_name: str | None
    quantity_sum: int
    first_line_index: int
    occurrence_count: int


@dataclass(frozen=True)
class MappingCandidateInput:
    """Ranked co-purchase mapping between a source SKU and a target SKU."""

    source_product_code: str
    recommended_product_code: str
    recommendation_rank: int
    recommended_product_name: str | None = None


@dataclass(frozen=True)
class CatalogueItemInput:
    """Explicit catalogue metadata for a product SKU."""

    product_code: str
    product_name: str
    is_rx: bool | None = None
    is_active: bool | None = None
    available_quantity: int | None = None
    unit_price: Decimal | None = None
    currency: str = "INR"
    image_url: str | None = None
    product_url: str | None = None
    metadata_source: str = "WORKBOOK_ONLY"
    is_synthetic_metadata: bool = False


@dataclass(frozen=True)
class SelectedRecommendation:
    """Final selected recommendation with full provenance."""

    selection_sequence: int
    recommended_product_code: str
    recommended_product_name: str
    source_product_code: str
    source_product_name: str
    original_mapping_rank: int
    selection_phase: SelectionPhase
    unit_price: str | None = None
    currency: str = "INR"
    metadata_source: str = "WORKBOOK_ONLY"
    is_synthetic_metadata: bool = False


@dataclass
class CandidateTraceEntry:
    """Complete audit trace for a single candidate mapping evaluated by the engine."""

    candidate_product_code: str
    candidate_product_name: str
    source_product_code: str
    original_mapping_rank: int
    is_eligible: bool
    metadata_source: str | None = None
    is_synthetic_metadata: bool = False
    eligibility_rejection_reasons: list[CandidateExclusionReason] = field(default_factory=list)
    rejection_reasons: list[CandidateExclusionReason] = field(default_factory=list)
    excluded_as_purchased: bool = False
    examined_during_allocation: bool = False
    allocation_decision: CandidateAllocationDecision = CandidateAllocationDecision.NOT_REACHED
    excluded_as_duplicate: bool = False
    duplicate_retained_from_source: str | None = None
    duplicate_retained_rank: int | None = None
    duplicate_retained_sequence: int | None = None
    selected: bool = False
    selection_phase: SelectionPhase | None = None
    selection_sequence: int | None = None


@dataclass
class SourceTraceEntry:
    """Complete audit trace for a distinct ordered source SKU with separate eligibility and allocation counters."""

    product_code: str
    product_name: str
    first_line_index: int
    quantity_sum: int
    mapping_found: bool
    status_reason: str | None
    mapping_count: int
    eligibility_evaluations_count: int
    eligible_candidate_count: int
    rejected_candidate_count: int
    source_priority_position: int | None
    priority_key_used: list[int | str] | None
    initial_quota: int
    allocation_candidates_examined: int
    initial_allocation_selected_count: int
    backfill_selected_count: int
    candidates_selected: int
    duplicate_candidate_count: int
    unvisited_candidate_count: int
    metadata_source: str | None = None
    is_synthetic_metadata: bool = False
    selected_skus: list[str] = field(default_factory=list)
    rejected_skus: list[str] = field(default_factory=list)
    duplicate_skus: list[str] = field(default_factory=list)
    unvisited_skus: list[str] = field(default_factory=list)


@dataclass
class OrderSummaryTrace:
    """Order-level summary inside the decision trace."""

    order_id: str
    original_order_line_count: int
    distinct_source_sku_count: int
    mapped_source_sku_count: int
    unmapped_source_sku_count: int
    purchased_sku_set: list[str]
    total_quantity_per_source: dict[str, int]
    first_line_index_per_source: dict[str, int]
    catalogue_metadata_sources_used: list[str] = field(default_factory=list)
    contains_synthetic_metadata: bool = False


@dataclass
class DecisionTrace:
    """Full structured, JSON-serializable decision trace for an order recommendation run."""

    order_summary: OrderSummaryTrace
    source_evaluations: list[SourceTraceEntry]
    candidate_evaluations: list[CandidateTraceEntry]
    selected_recommendation_count: int
    ordered_recommended_skus: list[str]
    selection_provenance: list[SelectedRecommendation]
    unfilled_recommendation_slots: int
    final_result_status: RecommendationResultStatus


@dataclass
class RecommendationResult:
    """Top-level return value of the deterministic recommendation engine."""

    order_id: str
    status: RecommendationResultStatus
    recommendations: list[SelectedRecommendation]
    unfilled_slots: int
    trace: DecisionTrace

    def to_dict(self) -> dict[str, Any]:
        """Convert result and trace into plain JSON-compatible Python primitives."""
        return json.loads(self.to_canonical_json())

    def to_canonical_json(self) -> str:
        """Serialize to deterministic, byte-for-byte reproducible JSON."""
        return json.dumps(
            asdict(self),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
