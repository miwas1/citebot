"""Typed contracts for reusable document-analysis work products."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceLink(BaseModel):
    """A field-level link to a source or deterministic derived result."""

    claim_id: str | None = None
    chunk_id: str | None = None
    document_id: str | None = None
    source_uri: str | None = None
    location_marker: str | None = None
    page: int | None = None
    quoted_support: str | None = None
    anchor_ids: list[str] = Field(default_factory=list)
    calculation_run_id: str | None = None
    relation: Literal["supports", "contradicts", "qualifies", "derived"] = "supports"


class WorkProductFinding(BaseModel):
    """Common reviewable finding shape used by initial workflow packs."""

    finding_id: str
    field: str
    value: Any = None
    status: Literal[
        "supported",
        "contradicted",
        "insufficient",
        "uncertain",
        "missing",
        "needs_review",
    ] = "needs_review"
    importance: Literal["critical", "material", "supporting", "incidental"] = "material"
    confidence: float = 0.0
    evidence: list[EvidenceLink] = Field(default_factory=list)
    reviewer_note: str | None = None


class WorkProduct(BaseModel):
    """Portable, schema-versioned result for a business workflow."""

    work_product_id: str
    analysis_run_id: str
    workflow_id: str
    workflow_version: str
    schema_version: str
    title: str
    status: Literal["draft", "needs_review", "approved", "rejected", "superseded"] = "draft"
    findings: list[WorkProductFinding] = Field(default_factory=list)
    summary: str = ""
    limitations: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowManifest(BaseModel):
    """Declarative policy for one analysis workflow."""

    workflow_id: str
    version: str
    title: str
    description: str
    prohibited_uses: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    always_review: bool = False
    critical_fields: list[str] = Field(default_factory=list)
    schema_version: str = "v1"
    query_template: str | None = None
    retrieval_filters: dict[str, Any] = Field(default_factory=dict)
    verification_policy: dict[str, Any] = Field(default_factory=dict)
    output_schema: str | None = None
    evaluation_dataset: str | None = None


class WorkflowRunRequest(BaseModel):
    """Request to turn a grounded research run into a work product."""

    workflow_id: str
    query: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    allow_python_execution: bool = False


class WorkflowRunResponse(BaseModel):
    """Workflow output plus the underlying research response."""

    product: WorkProduct
    research: dict[str, Any]


class ReviewDecisionRequest(BaseModel):
    """Review action with a hash to prevent lost edits."""

    action: Literal["approve", "edit", "reject", "request_reanalysis", "waive_with_reason"]
    actor_id: str = Field(default="local-reviewer", min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=4000)
    expected_hash: str | None = Field(default=None, min_length=64, max_length=64)
    edits: dict[str, Any] = Field(default_factory=dict)


class ReviewResumeRequest(BaseModel):
    """Request to resume a review-gated product after a reviewer decision."""

    actor_id: str = Field(default="local-reviewer", min_length=1, max_length=128)
    comment: str | None = Field(default=None, max_length=4000)
    expected_hash: str | None = Field(default=None, min_length=64, max_length=64)
