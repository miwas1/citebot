"""ORM models for documents, chunks, ingestion jobs, and research sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
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
