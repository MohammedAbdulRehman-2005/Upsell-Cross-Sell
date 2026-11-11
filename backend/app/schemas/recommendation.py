"""Pydantic v2 schemas for recommendation engine inputs, outputs, and decision traces."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.services.recommendation.models import (
    CandidateAllocationDecision,
    CandidateExclusionReason,
    RecommendationResultStatus,
    SelectionPhase,
)


class SelectedRecommendationSchema(BaseModel):
    """Pydantic schema for a selected product recommendation and its provenance."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    selection_sequence: int = Field(..., ge=1, le=6)
    recommended_product_code: str
    recommended_product_name: str
    source_product_code: str
    source_product_name: str
    original_mapping_rank: int = Field(..., ge=1, le=6)
    selection_phase: SelectionPhase
    unit_price: str | None = None
    currency: str = "INR"
    metadata_source: str = "WORKBOOK_ONLY"
    is_synthetic_metadata: bool = False


class CandidateTraceEntrySchema(BaseModel):
    """Pydantic schema for a single candidate evaluation trace."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    candidate_product_code: str
    candidate_product_name: str
    source_product_code: str
    original_mapping_rank: int = Field(..., ge=1, le=6)
    is_eligible: bool
    metadata_source: str | None = None
    is_synthetic_metadata: bool = False
    eligibility_rejection_reasons: list[CandidateExclusionReason]
    rejection_reasons: list[CandidateExclusionReason]
    excluded_as_purchased: bool
    examined_during_allocation: bool
    allocation_decision: CandidateAllocationDecision
    excluded_as_duplicate: bool
    duplicate_retained_from_source: str | None = None
    duplicate_retained_rank: int | None = None
    duplicate_retained_sequence: int | None = None
    selected: bool
    selection_phase: SelectionPhase | None = None
    selection_sequence: int | None = None


class SourceTraceEntrySchema(BaseModel):
    """Pydantic schema for a single ordered source evaluation trace."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    product_code: str
    product_name: str
    first_line_index: int = Field(..., ge=0)
    quantity_sum: int = Field(..., ge=1)
    mapping_found: bool
    status_reason: str | None = None
    mapping_count: int = Field(..., ge=0)
    eligibility_evaluations_count: int = Field(..., ge=0)
    eligible_candidate_count: int = Field(..., ge=0)
    rejected_candidate_count: int = Field(..., ge=0)
    source_priority_position: int | None = None
    priority_key_used: list[int | str] | None = None
    initial_quota: int = Field(..., ge=0, le=6)
    allocation_candidates_examined: int = Field(..., ge=0)
    initial_allocation_selected_count: int = Field(..., ge=0)
    backfill_selected_count: int = Field(..., ge=0)
    candidates_selected: int = Field(..., ge=0)
    duplicate_candidate_count: int = Field(..., ge=0)
    unvisited_candidate_count: int = Field(..., ge=0)
    metadata_source: str | None = None
    is_synthetic_metadata: bool = False
    selected_skus: list[str]
    rejected_skus: list[str]
    duplicate_skus: list[str]
    unvisited_skus: list[str]


class OrderSummaryTraceSchema(BaseModel):
    """Pydantic schema for the order summary inside the decision trace."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    order_id: str
    original_order_line_count: int = Field(..., ge=0)
    distinct_source_sku_count: int = Field(..., ge=0)
    mapped_source_sku_count: int = Field(..., ge=0)
    unmapped_source_sku_count: int = Field(..., ge=0)
    purchased_sku_set: list[str]
    total_quantity_per_source: dict[str, int]
    first_line_index_per_source: dict[str, int]
    catalogue_metadata_sources_used: list[str] = Field(default_factory=list)
    contains_synthetic_metadata: bool = False


class DecisionTraceSchema(BaseModel):
    """Pydantic schema for the complete recommendation decision trace."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    order_summary: OrderSummaryTraceSchema
    source_evaluations: list[SourceTraceEntrySchema]
    candidate_evaluations: list[CandidateTraceEntrySchema]
    selected_recommendation_count: int = Field(..., ge=0, le=6)
    ordered_recommended_skus: list[str]
    selection_provenance: list[SelectedRecommendationSchema]
    unfilled_recommendation_slots: int = Field(..., ge=0, le=6)
    final_result_status: RecommendationResultStatus


class RecommendationResultSchema(BaseModel):
    """Pydantic schema for the top-level recommendation engine result."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    order_id: str
    status: RecommendationResultStatus
    recommendations: list[SelectedRecommendationSchema]
    unfilled_slots: int = Field(..., ge=0, le=6)
    trace: DecisionTraceSchema
