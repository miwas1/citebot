"""Persistence helpers for ingestion jobs, documents, and chunks."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select

from app.db.models import ChunkRecord, DocumentRecord, IngestionJobRecord
from app.db.session import DatabaseSessionManager
from app.ingestion.schemas import (
    CanonicalDocument,
    ChunkPayload,
    DocumentState,
    IngestionMetrics,
    JobStatusResponse,
)


class IngestionRepository:
    """Store and query ingestion metadata in the primary database."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        """Bind the repository to the shared session manager."""

        self._session_manager = session_manager

    async def create_job(
        self,
        job_id: str,
        source_path: str,
        force_reindex: bool,
        embedding_version: str,
        index_version: str,
    ) -> None:
        """Persist a newly started ingestion job."""

        async with self._session_manager.session() as session:
            session.add(
                IngestionJobRecord(
                    job_id=job_id,
                    source_path=source_path,
                    status="running",
                    force_reindex=force_reindex,
                    embedding_version=embedding_version,
                    index_version=index_version,
                )
            )

    async def complete_job(
        self,
        job_id: str,
        documents_seen: int,
        documents_indexed: int,
        documents_skipped: int,
        chunks_written: int,
    ) -> None:
        """Mark an ingestion job as completed and persist aggregate counts."""

        async with self._session_manager.session() as session:
            record = await session.get(IngestionJobRecord, job_id)
            if record is None:
                return
            record.status = "completed"
            record.completed_at = datetime.now(tz=UTC)
            record.documents_seen = documents_seen
            record.documents_indexed = documents_indexed
            record.documents_skipped = documents_skipped
            record.chunks_written = chunks_written

    async def fail_job(
        self,
        job_id: str,
        documents_seen: int,
        documents_indexed: int,
        documents_skipped: int,
        chunks_written: int,
        error_message: str,
    ) -> None:
        """Mark an ingestion job as failed and store the captured error message."""

        async with self._session_manager.session() as session:
            record = await session.get(IngestionJobRecord, job_id)
            if record is None:
                return
            record.status = "failed"
            record.completed_at = datetime.now(tz=UTC)
            record.documents_seen = documents_seen
            record.documents_indexed = documents_indexed
            record.documents_skipped = documents_skipped
            record.chunks_written = chunks_written
            record.error_message = error_message

    async def get_job(self, job_id: str) -> JobStatusResponse | None:
        """Return a single ingestion job if it exists."""

        async with self._session_manager.session() as session:
            record = await session.get(IngestionJobRecord, job_id)
            if record is None:
                return None
            return self._to_job_response(record)

    async def get_document_state(self, source_uri: str) -> DocumentState | None:
        """Return the stored document hash for the given source URI."""

        async with self._session_manager.session() as session:
            result = await session.execute(
                select(DocumentRecord).where(DocumentRecord.source_uri == source_uri)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            return DocumentState(
                document_id=record.document_id,
                source_uri=record.source_uri,
                content_hash=record.content_hash,
            )

    async def save_document(
        self,
        document: CanonicalDocument,
        chunks: list[ChunkPayload],
        raw_text_path: str,
    ) -> None:
        """Upsert a document and replace all of its chunk metadata atomically."""

        async with self._session_manager.session() as session:
            record = await session.get(DocumentRecord, document.document_id)
            if record is None:
                record = DocumentRecord(
                    document_id=document.document_id,
                    source_uri=document.source_uri,
                    title=document.title,
                    publisher=document.publisher,
                    published_at=document.published_at,
                    ingested_at=document.ingested_at,
                    content_hash=document.content_hash,
                    access_policy=document.access_policy,
                    raw_text_path=raw_text_path,
                    metadata_json=document.metadata,
                )
                session.add(record)
            else:
                record.source_uri = document.source_uri
                record.title = document.title
                record.publisher = document.publisher
                record.published_at = document.published_at
                record.ingested_at = document.ingested_at
                record.content_hash = document.content_hash
                record.access_policy = document.access_policy
                record.raw_text_path = raw_text_path
                record.metadata_json = document.metadata
            await session.execute(
                delete(ChunkRecord).where(
                    ChunkRecord.document_id == document.document_id
                )
            )
            session.add_all(
                [
                    ChunkRecord(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        text=chunk.text,
                        token_count=chunk.token_count,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        section=chunk.section,
                        page=chunk.page,
                        location_marker=chunk.location_marker,
                        embedding_model=chunk.embedding_model,
                        embedding_version=chunk.embedding_version,
                        index_version=chunk.index_version,
                    )
                    for chunk in chunks
                ]
            )

    async def metrics(self) -> IngestionMetrics:
        """Return aggregate counts for ingestion observability."""

        async with self._session_manager.session() as session:
            documents = await session.scalar(
                select(func.count()).select_from(DocumentRecord)
            )
            chunks = await session.scalar(select(func.count()).select_from(ChunkRecord))
            jobs = await session.scalar(
                select(func.count()).select_from(IngestionJobRecord)
            )
            return IngestionMetrics(
                documents=documents or 0, chunks=chunks or 0, jobs=jobs or 0
            )

    def _to_job_response(self, record: IngestionJobRecord) -> JobStatusResponse:
        """Convert a job ORM record into the API response model."""

        return JobStatusResponse(
            job_id=record.job_id,
            source_path=record.source_path,
            status=record.status,
            force_reindex=record.force_reindex,
            embedding_version=record.embedding_version,
            index_version=record.index_version,
            started_at=record.started_at,
            completed_at=record.completed_at,
            error_message=record.error_message,
            documents_seen=record.documents_seen,
            documents_indexed=record.documents_indexed,
            documents_skipped=record.documents_skipped,
            chunks_written=record.chunks_written,
        )
