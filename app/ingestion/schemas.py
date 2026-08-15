"""Pydantic schemas for ingestion workflows and admin APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_PROJECT_ID = "sample-project"


class LoadedDocument(BaseModel):
    """Raw document content loaded from the corpus source."""

    source_uri: str
    title: str
    text: str
    publisher: str | None = None
    published_at: datetime | None = None
    access_policy: str = "internal"
    metadata: dict[str, Any] = Field(default_factory=dict)
    structured: StructuredDocument | None = None


class DocumentElement(BaseModel):
    """A citation-addressable element extracted from one document page."""

    element_id: str
    element_type: str = "paragraph"
    text: str = ""
    markdown: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    reading_order: int = 0
    section_path: list[str] = Field(default_factory=list)
    confidence: float | None = None
    source_engine: str = "native"
    char_start: int | None = None
    char_end: int | None = None
    parent_element_id: str | None = None
    child_element_ids: list[str] = Field(default_factory=list)
    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None
    row_span: int = 1
    column_span: int = 1
    attributes: dict[str, Any] = Field(default_factory=dict)
    relationships: dict[str, list[str]] = Field(default_factory=dict)


class DocumentTable(BaseModel):
    """Canonical table grid retained for search and deterministic calculation."""

    table_id: str
    page_numbers: list[int] = Field(default_factory=list)
    caption: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    source_element_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class StructuredPage(BaseModel):
    """Page-level extraction metadata and ordered elements."""

    page_number: int
    width: float | None = None
    height: float | None = None
    rotation: int = 0
    extraction_method: str = "native"
    native_text_coverage: float = 1.0
    ocr_confidence: float | None = None
    image_hash: str | None = None
    quality_issues: list[dict[str, Any]] = Field(default_factory=list)
    elements: list[DocumentElement] = Field(default_factory=list)


class StructuredDocument(BaseModel):
    """Versioned canonical representation retained alongside flattened text."""

    schema_version: str = "structured-v2"
    document_id: str | None = None
    media_type: str | None = None
    parser_version: str = "native-v1"
    language: str | None = None
    pages: list[StructuredPage] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    extraction_issues: list[dict[str, Any]] = Field(default_factory=list)
    source_content_hash: str | None = None
    parser_config_hash: str | None = None
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    relationships: list[dict[str, Any]] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    """Normalized document ready for persistence and chunking."""

    document_id: str
    project_id: str = DEFAULT_PROJECT_ID
    source_uri: str
    title: str
    text: str
    publisher: str | None = None
    published_at: datetime | None = None
    ingested_at: datetime
    content_hash: str
    access_policy: str = "internal"
    metadata: dict[str, Any] = Field(default_factory=dict)
    structured: StructuredDocument | None = None


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
    element_ids: list[str] = Field(default_factory=list)
    bbox_refs: list[tuple[float, float, float, float]] = Field(default_factory=list)
    extraction_method: str | None = None
    min_confidence: float | None = None
    embedding_model: str
    embedding_version: str
    index_version: str
    parent_chunk_id: str | None = None
    chunk_level: Literal["document", "section", "element", "table", "row", "window"] = "window"
    heading_path: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    version_id: str | None = None
    is_current: bool = True
    ordinal: int = 0
    source_anchor_ids: list[str] = Field(default_factory=list)


class DocumentState(BaseModel):
    """Persisted state used for idempotent ingestion decisions."""

    document_id: str
    source_uri: str
    content_hash: str


class IngestionRequest(BaseModel):
    """Request body for running a local or admin ingestion job."""

    source_path: str = Field(min_length=1, max_length=4096)
    project_id: str = DEFAULT_PROJECT_ID
    force_reindex: bool = False
    embedding_version: str = "bge-small-en-v1.5"
    index_version: str = "v2"


class JobStatusResponse(BaseModel):
    """External representation of ingestion job status and progress."""

    job_id: str
    project_id: str = DEFAULT_PROJECT_ID
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
    attempt_count: int = 0
    max_attempts: int = 3
    stage: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    lease_expires_at: datetime | None = None


class SearchRequest(BaseModel):
    """Request body for dense, sparse, or hybrid retrieval over ingested chunks."""

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=50)
    strategy: Literal["sparse", "dense", "hybrid"] = "hybrid"
    index_target: Literal["auto", "pgvector", "qdrant", "local"] = "auto"
    filters: RetrievalFilters = Field(default_factory=lambda: RetrievalFilters())
    include_explain: bool = False
    enable_reranking: bool | None = None


class RetrievalFilters(BaseModel):
    """Metadata filters applied consistently across retrieval backends."""

    document_ids: list[str] = Field(default_factory=list)
    project_id: str = DEFAULT_PROJECT_ID
    source_uris: list[str] = Field(default_factory=list)
    access_policies: list[str] = Field(default_factory=list)
    embedding_version: str | None = None
    index_version: str | None = None
    version_ids: list[str] = Field(default_factory=list)
    current_only: bool = True
    chunk_levels: list[str] = Field(default_factory=list)


class SearchResult(BaseModel):
    """Retrieval result returned for dense, sparse, and hybrid search workflows."""

    chunk_id: str
    document_id: str
    project_id: str = DEFAULT_PROJECT_ID
    title: str
    source_uri: str
    location_marker: str | None = None
    page: int | None = None
    element_ids: list[str] = Field(default_factory=list)
    bbox_refs: list[list[float]] = Field(default_factory=list)
    extraction_method: str | None = None
    min_confidence: float | None = None
    score: float
    text: str
    dense_score: float | None = None
    sparse_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    source_backend: str = "sparse"
    metadata: dict[str, Any] = Field(default_factory=dict)
    explain: dict[str, Any] | None = None
    parent_chunk_id: str | None = None
    chunk_level: str = "window"
    heading_path: list[str] = Field(default_factory=list)
    version_id: str | None = None
    is_current: bool = True
    source_anchor_ids: list[str] = Field(default_factory=list)


class IngestionMetrics(BaseModel):
    """Repository-level ingestion counts for observability endpoints."""

    documents: int
    chunks: int
    jobs: int


class DocumentSummary(BaseModel):
    """Document metadata presented by the end-user library."""

    document_id: str
    project_id: str = DEFAULT_PROJECT_ID
    title: str
    source_uri: str
    content_hash: str
    ingested_at: datetime
    chunk_count: int = 0
    media_type: str | None = None
    size_bytes: int | None = None


class DocumentVersionSummary(BaseModel):
    """Public immutable version metadata for revision-aware workflows."""

    version_id: str
    logical_document_id: str
    document_id: str
    predecessor_version_id: str | None = None
    content_hash: str
    version_label: str | None = None
    effective_at: datetime | None = None
    superseded_at: datetime | None = None
    is_current: bool
    parser_name: str
    parser_version: str
    schema_version: str
    page_count: int | None = None
    language: str | None = None
    created_at: datetime


class UploadResponse(BaseModel):
    """Accepted browser upload and its ingestion job."""

    upload_id: str
    filename: str
    size_bytes: int
    job: JobStatusResponse


# ``LoadedDocument`` is declared before the structured models for API readability;
# rebuild its forward reference once all schema classes exist.
LoadedDocument.model_rebuild()
