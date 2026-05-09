"""Typed models for evaluation datasets, runs, and exported artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.agents.schemas import ConversationTurn


class EvaluationCase(BaseModel):
    """One versioned evaluation case executed against the real research pipeline."""

    eval_case_id: str
    question: str
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    expected_answer_traits: list[str] = Field(default_factory=list)
    reference_answer: str | None = None
    expected_document_ids: list[str] = Field(default_factory=list)
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_source_uris: list[str] = Field(default_factory=list)
    expected_citation_rules: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    owner: str = "citebot"
    top_k: int = Field(default=5, ge=1, le=20)
    allow_web_search: bool = False
    allow_python_execution: bool = False
    freshness_required: bool = False


class EvaluationDataset(BaseModel):
    """A versioned collection of evaluation cases loaded from disk."""

    dataset_name: str
    dataset_version: str
    owner: str = "citebot"
    cases: list[EvaluationCase] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationRunRequest(BaseModel):
    """Request parameters for an evaluation run."""

    dataset_path: Path | None = None
    source_path: Path | None = None
    force_reindex: bool = False
    embedding_version: str = "default"
    index_version: str = "default"
    run_ragas: bool = False
    threshold_mode: Literal["report", "ci"] = "report"


class RagasEvaluationSummary(BaseModel):
    """Run-level RAGAS execution summary with non-fatal skip behavior."""

    status: Literal["disabled", "skipped", "completed", "failed"] = "disabled"
    scores: dict[str, float] = Field(default_factory=dict)
    message: str | None = None
    evaluator_provider: str | None = None
    evaluator_model: str | None = None


class TraceExportSummary(BaseModel):
    """Trace export manifest information for downstream Phoenix ingestion."""

    status: Literal["disabled", "recorded"] = "disabled"
    endpoint: str | None = None
    trace_count: int = 0
    artifact_path: str | None = None


class EvaluationCaseResult(BaseModel):
    """Stored result for one executed evaluation case."""

    eval_case_id: str
    session_id: str
    trace_id: str
    question: str
    direct_answer: str
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retrieved_document_ids: list[str] = Field(default_factory=list)
    retrieved_source_uris: list[str] = Field(default_factory=list)
    citation_chunk_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    threshold_failures: list[str] = Field(default_factory=list)
    passed: bool = True
    error: str | None = None


class EvaluationRunResult(BaseModel):
    """Persisted aggregate result for one evaluation run."""

    run_id: str = Field(default_factory=lambda: uuid4().hex)
    status: Literal["completed", "failed"] = "completed"
    dataset_name: str
    dataset_version: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    threshold_mode: Literal["report", "ci"] = "report"
    run_ragas: bool = False
    artifact_path: str | None = None
    case_results: list[EvaluationCaseResult] = Field(default_factory=list)
    summary_metrics: dict[str, float] = Field(default_factory=dict)
    threshold_failures: list[str] = Field(default_factory=list)
    ragas: RagasEvaluationSummary = Field(default_factory=RagasEvaluationSummary)
    trace_export: TraceExportSummary = Field(default_factory=TraceExportSummary)
    metadata: dict[str, Any] = Field(default_factory=dict)


def artifact_file_name(run_id: str) -> str:
    """Return the stable artifact filename for one evaluation run."""

    return f"{run_id}.json"
