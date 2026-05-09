"""Pydantic schemas for ingestion workflows and admin APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoadedDocument(BaseModel):
    """Raw document content loaded from the corpus source."""

    source_uri: str
    title: str
    text: str
    publisher: str | None = None
    published_at: datetime | None = None
    access_policy: str = "internal"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalDocument(BaseModel):
    """Normalized document ready for persistence and chunking."""

    document_id: str
    source_uri: str
    title: str
    text: str
    publisher: str | None = None
    published_at: datetime | None = None
    ingested_at: datetime
    content_hash: str
    access_policy: str = "internal"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkPayload(BaseModel):
    """Normalized chunk metadata written to the metadata and index backends."""

    chunk_id: str
    document_id: str
    source_uri: str
    title: str
    text: str
    token_count: int
    char_start: int
    char_end: int
    section: str | None = None
    page: int | None = None
    location_marker: str | None = None
    embedding_model: str
    embedding_version: str
    index_version: str


class DocumentState(BaseModel):
    """Persisted state used for idempotent ingestion decisions."""

    document_id: str
    source_uri: str
    content_hash: str


class IngestionRequest(BaseModel):
    """Request body for running a local or admin ingestion job."""

    source_path: str
    force_reindex: bool = False
    embedding_version: str = "v1"
    index_version: str = "v1"


class JobStatusResponse(BaseModel):
    """External representation of ingestion job status and progress."""

    job_id: str
    source_path: str
    status: str
    force_reindex: bool
    embedding_version: str
    index_version: str
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    documents_seen: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    chunks_written: int = 0


class SearchRequest(BaseModel):
    """Request body for sparse search preview over ingested chunks."""

    query: str
    top_k: int = 5


class SearchResult(BaseModel):
    """Sparse search result returned for local corpus validation."""

    chunk_id: str
    document_id: str
    title: str
    source_uri: str
    location_marker: str | None = None
    score: float
    text: str


class IngestionMetrics(BaseModel):
    """Repository-level ingestion counts for observability endpoints."""

    documents: int
    chunks: int
    jobs: int
