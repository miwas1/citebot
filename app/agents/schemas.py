"""Typed request, response, state, and memory models for research workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """One persisted user or assistant turn in a research session."""

    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    citation_ids: list[str] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    trace_id: str | None = None


class RetrievalPlan(BaseModel):
    """Planned retrieval and tool actions for the current query."""

    retrieval_required: bool = True
    top_k: int = 5
    use_web_search: bool = False
    use_python: bool = False
    insufficient_context: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class ResearchContext(BaseModel):
    """Normalized internal, web, or analysis context used for answer generation."""

    chunk_id: str
    document_id: str
    title: str
    source_uri: str
    location_marker: str | None = None
    text: str
    score: float
    source_backend: str
    source_type: Literal["internal", "web", "analysis"] = "internal"
    fetched_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(BaseModel):
    """Auditable metadata for one tool execution attempt."""

    tool_name: str
    status: Literal["completed", "failed", "skipped"]
    input_summary: str
    output_summary: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = 0.0
    error_message: str | None = None
    trace_id: str | None = None


class Citation(BaseModel):
    """Citation metadata attached to a generated answer."""

    citation_id: str
    chunk_id: str
    document_id: str
    title: str
    source_uri: str
    location_marker: str | None = None
    source_type: Literal["internal", "web", "analysis"] = "internal"
    support_span: str
    quoted_support: str
    fetched_at: datetime | None = None


class ResearchAnswer(BaseModel):
    """Structured answer payload returned to API consumers."""

    direct_answer: str
    supporting_evidence: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    limitations: str | None = None
    follow_up_suggestion: str | None = None


class ClaimVerification(BaseModel):
    """Verdict for one claim-to-citation mapping."""

    citation_id: str
    claim_text: str
    supporting_chunk_ids: list[str] = Field(default_factory=list)
    verdict: Literal["supported", "partially_supported", "unsupported", "stale"]
    confidence: float
    failure_reason: str | None = None


class CitationVerificationResult(BaseModel):
    """Aggregate verification result for a generated answer."""

    overall_verdict: Literal["supported", "partially_supported", "unsupported"]
    claims: list[ClaimVerification] = Field(default_factory=list)
    unsupported_citation_ids: list[str] = Field(default_factory=list)


class ResearchMemory(BaseModel):
    """Compressed conversational memory with citation provenance."""

    summary: str = ""
    recent_turns: list[ConversationTurn] = Field(default_factory=list)
    citation_graph: dict[str, list[str]] = Field(default_factory=dict)
    unresolved_constraints: list[str] = Field(default_factory=list)


class ResearchQueryRequest(BaseModel):
    """Request body for the research query API."""

    session_id: str | None = None
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    allow_web_search: bool | None = None
    allow_python_execution: bool | None = None
    freshness_required: bool = False
    analysis_code: str | None = None
    analysis_inputs: dict[str, Any] = Field(default_factory=dict)
    include_debug_trace: bool = False


class ResearchResponse(BaseModel):
    """Response body for one research agent execution."""

    session_id: str
    trace_id: str
    answer: ResearchAnswer
    verification: CitationVerificationResult
    memory: ResearchMemory
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    state_transitions: list[str] = Field(default_factory=list)
    retrieved_contexts: list[ResearchContext] = Field(default_factory=list)
    error: str | None = None


class ResearchSessionRecord(BaseModel):
    """Persisted session data used to replay and continue prior conversations."""

    session_id: str
    turns: list[ConversationTurn] = Field(default_factory=list)
    memory: ResearchMemory = Field(default_factory=ResearchMemory)
    last_trace_id: str | None = None


class ResearchGenerationRequest(BaseModel):
    """Internal request passed to answer generators."""

    query: str
    trace_id: str
    memory: ResearchMemory
    contexts: list[ResearchContext] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)


class PythonSandboxExecution(BaseModel):
    """Request payload for sandboxed code execution."""

    code: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    trace_id: str


class PythonSandboxResult(BaseModel):
    """Structured result from a sandboxed Python execution."""

    stdout: str = ""
    stderr: str = ""
    result_json: dict[str, Any] = Field(default_factory=dict)
    terminated_reason: str | None = None
    runtime_ms: float = 0.0


def create_trace_id() -> str:
    """Return a stable unique identifier for one graph execution."""

    return uuid4().hex


def create_session_id() -> str:
    """Return a stable unique identifier for a new session."""

    return uuid4().hex
