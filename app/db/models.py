"""ORM models for documents, evidence, workflows, and research sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    """Return a timezone-aware current UTC timestamp."""

    return datetime.now(tz=UTC)


class DocumentRecord(Base):
    """Persisted source document metadata."""

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_uri: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    access_policy: Mapped[str] = mapped_column(String(128), default="internal")
    raw_text_path: Mapped[str] = mapped_column(String(1024))
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    chunks: Mapped[list[ChunkRecord]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ChunkRecord(Base):
    """Persisted chunk metadata for citation traceability."""

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE")
    )
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_marker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    element_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    bbox_refs: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    extraction_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    min_confidence: Mapped[float | None] = mapped_column(nullable=True)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    chunk_level: Mapped[str] = mapped_column(String(32), default="window")
    heading_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    source_anchor_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(255))
    embedding_version: Mapped[str] = mapped_column(String(64))
    index_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    document: Mapped[DocumentRecord] = relationship(back_populates="chunks")


class IngestionJobRecord(Base):
    """Persisted ingestion and re-index job state."""

    __tablename__ = "ingestion_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), index=True)
    force_reindex: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding_version: Mapped[str] = mapped_column(String(64))
    index_version: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    documents_seen: Mapped[int] = mapped_column(Integer, default=0)
    documents_indexed: Mapped[int] = mapped_column(Integer, default=0)
    documents_skipped: Mapped[int] = mapped_column(Integer, default=0)
    chunks_written: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)


class ResearchSessionRecordModel(Base):
    """Persisted conversation state for replayable research sessions."""

    __tablename__ = "research_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    turns_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    memory_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    last_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DocumentVersionRecord(Base):
    """Immutable logical-document version metadata."""

    __tablename__ = "document_versions"
    __table_args__ = (
        Index("ix_document_versions_logical_current", "logical_document_id", "is_current"),
    )

    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    logical_document_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), index=True
    )
    predecessor_version_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("document_versions.version_id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    version_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    parser_name: Mapped[str] = mapped_column(String(128), default="native")
    parser_version: Mapped[str] = mapped_column(String(64), default="v1")
    schema_version: Mapped[str] = mapped_column(String(64), default="structured-v1")
    source_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceAnchorRecord(Base):
    """Deterministic link from derived content back to source layout."""

    __tablename__ = "source_anchors"
    __table_args__ = (
        Index("ix_source_anchors_version_element", "version_id", "element_id"),
        Index("ix_source_anchors_chunk", "chunk_id"),
    )

    anchor_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.version_id", ondelete="CASCADE"), index=True
    )
    element_id: Mapped[str] = mapped_column(String(128))
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("chunks.chunk_id", ondelete="SET NULL"), nullable=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    anchor_kind: Mapped[str] = mapped_column(String(32), default="observed")
    extraction_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)


class AnalysisRunRecord(Base):
    """Reproducible execution metadata for research and workflow runs."""

    __tablename__ = "analysis_runs"

    analysis_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str] = mapped_column(String(128), default="research")
    workflow_version: Mapped[str] = mapped_column(String(64), default="v1")
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    generator_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verifier_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_usage_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    quality_summary_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ClaimRecord(Base):
    """Atomic claim extracted from a generated answer."""

    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_analysis_order", "analysis_run_id", "claim_order"),)

    claim_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="CASCADE"), index=True
    )
    claim_order: Mapped[int] = mapped_column(Integer)
    claim_text: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    predicate: Mapped[str | None] = mapped_column(String(512), nullable=True)
    object_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    claim_type: Mapped[str] = mapped_column(String(32), default="factual")
    importance: Mapped[str] = mapped_column(String(32), default="material")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    confidence: Mapped[float] = mapped_column(default=0.0)


class ClaimEvidenceRecord(Base):
    """Verification result for one claim and one evidence anchor."""

    __tablename__ = "claim_evidence"

    claim_evidence_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("claims.claim_id", ondelete="CASCADE"), index=True
    )
    anchor_id: Mapped[str] = mapped_column(
        ForeignKey("source_anchors.anchor_id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(String(32), default="supports")
    verifier_name: Mapped[str] = mapped_column(String(128), default="deterministic")
    verifier_version: Mapped[str] = mapped_column(String(64), default="v1")
    entailment_score: Mapped[float | None] = mapped_column(nullable=True)
    contradiction_score: Mapped[float | None] = mapped_column(nullable=True)
    neutral_score: Mapped[float | None] = mapped_column(nullable=True)
    deterministic_checks_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    selected_for_output: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkProductRecord(Base):
    """Versioned, reviewable structured output."""

    __tablename__ = "work_products"

    work_product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(64))
    schema_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ReviewEventRecord(Base):
    """Append-only human review event."""

    __tablename__ = "review_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    work_product_id: Mapped[str] = mapped_column(
        ForeignKey("work_products.work_product_id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(96))
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ReviewCheckpointRecord(Base):
    """Durable review-gate checkpoint keyed by the analysis thread ID."""

    __tablename__ = "review_checkpoints"
    __table_args__ = (Index("ix_review_checkpoints_thread", "thread_id", unique=True),)

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(96), index=True)
    work_product_id: Mapped[str] = mapped_column(
        ForeignKey("work_products.work_product_id", ondelete="CASCADE"), index=True
    )
    state_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="needs_review", index=True)
    state_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CalculationRunRecord(Base):
    """Reproducible deterministic calculation execution."""

    __tablename__ = "calculation_runs"

    calculation_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.analysis_run_id", ondelete="SET NULL"), nullable=True
    )
    engine_name: Mapped[str] = mapped_column(String(64), default="duckdb")
    engine_version: Mapped[str] = mapped_column(String(64))
    plan_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    warnings_json: Mapped[list[object]] = mapped_column(JSON, default=list)
    reproducibility_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DocumentDiffRecord(Base):
    """Persisted exact and semantic comparison between two document versions."""

    __tablename__ = "document_diffs"

    diff_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    old_version_id: Mapped[str] = mapped_column(String(64), index=True)
    new_version_id: Mapped[str] = mapped_column(String(64), index=True)
    matcher_version: Mapped[str] = mapped_column(String(64), default="v1")
    status: Mapped[str] = mapped_column(String(32), default="completed")
    summary_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ElementDiffRecord(Base):
    """One matched or unmatched element change inside a document diff."""

    __tablename__ = "element_diffs"

    element_diff_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    diff_id: Mapped[str] = mapped_column(
        ForeignKey("document_diffs.diff_id", ondelete="CASCADE"), index=True
    )
    old_element_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    new_element_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    operation: Mapped[str] = mapped_column(String(32))
    exact_diff_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    semantic_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    impact: Mapped[str | None] = mapped_column(String(32), nullable=True)
    old_anchor_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    new_anchor_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
