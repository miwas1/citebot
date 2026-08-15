"""Persistence helpers for ingestion jobs, documents, and chunks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func, select, update

from app.db.models import (
    ChunkRecord,
    DocumentRecord,
    DocumentVersionRecord,
    IngestionJobRecord,
    SourceAnchorRecord,
)
from app.db.session import DatabaseSessionManager
from app.ingestion.provenance import anchor_id, text_hash
from app.ingestion.schemas import (
    DEFAULT_PROJECT_ID,
    CanonicalDocument,
    ChunkPayload,
    DocumentState,
    DocumentSummary,
    DocumentVersionSummary,
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
        project_id: str = DEFAULT_PROJECT_ID,
        status: str = "running",
        max_attempts: int = 3,
    ) -> None:
        """Persist a newly started ingestion job."""

        async with self._session_manager.session() as session:
            session.add(
                IngestionJobRecord(
                    job_id=job_id,
                    project_id=project_id,
                    source_path=source_path,
                    status=status,
                    force_reindex=force_reindex,
                    embedding_version=embedding_version,
                    index_version=index_version,
                    max_attempts=max_attempts,
                )
            )

    async def claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> JobStatusResponse | None:
        """Atomically claim the oldest queued job for a worker lease."""

        now = datetime.now(tz=UTC)
        lease_expires = now + timedelta(seconds=lease_seconds)
        async with self._session_manager.session() as session:
            record = await session.scalar(
                select(IngestionJobRecord)
                .where(IngestionJobRecord.status == "queued")
                .order_by(IngestionJobRecord.started_at)
                .limit(1)
            )
            if record is None:
                return None
            result = await session.execute(
                update(IngestionJobRecord)
                .where(
                    IngestionJobRecord.job_id == record.job_id,
                    IngestionJobRecord.status == "queued",
                )
                .values(
                    status="running",
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires,
                    heartbeat_at=now,
                    attempt_count=IngestionJobRecord.attempt_count + 1,
                    stage="claimed",
                )
            )
            if result.rowcount != 1:
                return None
            await session.flush()
            await session.refresh(record)
            return self._to_job_response(record)

    async def recover_stale_jobs(self) -> int:
        """Return expired running jobs to the queue or quarantine exhausted jobs."""

        now = datetime.now(tz=UTC)
        async with self._session_manager.session() as session:
            exhausted = await session.execute(
                update(IngestionJobRecord)
                .where(
                    IngestionJobRecord.status == "running",
                    IngestionJobRecord.lease_expires_at.is_not(None),
                    IngestionJobRecord.lease_expires_at < now,
                    IngestionJobRecord.attempt_count
                    >= IngestionJobRecord.max_attempts,
                )
                .values(
                    status="quarantined",
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    stage="quarantined",
                    completed_at=now,
                    error_message="Worker lease expired after max attempts",
                )
            )
            result = await session.execute(
                update(IngestionJobRecord)
                .where(
                    IngestionJobRecord.status == "running",
                    IngestionJobRecord.lease_expires_at.is_not(None),
                    IngestionJobRecord.lease_expires_at < now,
                    IngestionJobRecord.attempt_count
                    < IngestionJobRecord.max_attempts,
                )
                .values(
                    status="queued",
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    stage="requeued",
                )
            )
            return int((result.rowcount or 0) + (exhausted.rowcount or 0))

    async def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        """Extend a worker lease while a job is processing."""

        now = datetime.now(tz=UTC)
        async with self._session_manager.session() as session:
            await session.execute(
                update(IngestionJobRecord)
                .where(
                    IngestionJobRecord.job_id == job_id,
                    IngestionJobRecord.lease_owner == worker_id,
                    IngestionJobRecord.status == "running",
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
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
            record.stage = "completed"
            record.lease_owner = None
            record.lease_expires_at = None

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
            record.stage = "failed"
            record.lease_owner = None
            record.lease_expires_at = None

    async def get_job(self, job_id: str) -> JobStatusResponse | None:
        """Return a single ingestion job if it exists."""

        async with self._session_manager.session() as session:
            record = await session.get(IngestionJobRecord, job_id)
            if record is None:
                return None
            return self._to_job_response(record)

    async def has_active_or_completed_job(
        self, source_path: str, project_id: str = DEFAULT_PROJECT_ID
    ) -> bool:
        """Check whether a bootstrap source already has durable ingestion work."""

        source_paths = {source_path, str(Path(source_path).resolve())}
        async with self._session_manager.session() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(IngestionJobRecord)
                .where(
                    IngestionJobRecord.source_path.in_(source_paths),
                    IngestionJobRecord.project_id == project_id,
                    IngestionJobRecord.status.in_(["queued", "running", "completed"]),
                )
            )
        return bool(count)

    async def get_document_state(
        self, project_id: str, source_uri: str | None = None
    ) -> DocumentState | None:
        """Return the stored document hash for the given source URI."""

        if source_uri is None:
            source_uri = project_id
            project_id = DEFAULT_PROJECT_ID

        async with self._session_manager.session() as session:
            result = await session.execute(
                select(DocumentRecord).where(
                    DocumentRecord.project_id == project_id,
                    DocumentRecord.source_uri == source_uri,
                )
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
                    project_id=document.project_id,
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
                record.project_id = document.project_id
                record.title = document.title
                record.publisher = document.publisher
                record.published_at = document.published_at
                record.ingested_at = document.ingested_at
                record.content_hash = document.content_hash
                record.access_policy = document.access_policy
                record.raw_text_path = raw_text_path
                record.metadata_json = document.metadata
            await session.execute(
                update(ChunkRecord)
                .where(ChunkRecord.document_id == document.document_id)
                .values(is_current=False)
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
                        element_ids=chunk.element_ids,
                        bbox_refs=[list(box) for box in chunk.bbox_refs],
                        extraction_method=chunk.extraction_method,
                        min_confidence=chunk.min_confidence,
                        embedding_model=chunk.embedding_model,
                        embedding_version=chunk.embedding_version,
                        index_version=chunk.index_version,
                        parent_chunk_id=chunk.parent_chunk_id,
                        chunk_level=chunk.chunk_level,
                        heading_path=chunk.heading_path,
                        content_hash=chunk.content_hash,
                        version_id=chunk.version_id,
                        is_current=chunk.is_current,
                        ordinal=chunk.ordinal,
                        source_anchor_ids=chunk.source_anchor_ids,
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

    async def save_provenance(
        self,
        document: CanonicalDocument,
        chunks: list[ChunkPayload],
    ) -> None:
        """Persist an immutable content version and deterministic source anchors."""

        version_id = sha256(
            f"{document.project_id}:{document.content_hash}".encode()
        ).hexdigest()
        logical_document_id = document.document_id
        async with self._session_manager.session() as session:
            existing = await session.get(DocumentVersionRecord, version_id)
            if existing is None:
                predecessor_version_id = await self._latest_version_id(
                    session, logical_document_id
                )
                await session.execute(
                    update(DocumentVersionRecord)
                    .where(
                        DocumentVersionRecord.logical_document_id == logical_document_id,
                        DocumentVersionRecord.is_current.is_(True),
                    )
                    .values(is_current=False, superseded_at=datetime.now(tz=UTC))
                )
                structured = document.structured
                session.add(
                    DocumentVersionRecord(
                        version_id=version_id,
                        logical_document_id=logical_document_id,
                        document_id=document.document_id,
                        content_hash=document.content_hash,
                        predecessor_version_id=predecessor_version_id,
                        version_label=document.metadata.get("version_label"),
                        parser_name=structured.parser_version.split("-", 1)[0]
                        if structured
                        else "native",
                        parser_version=structured.parser_version if structured else "v1",
                        schema_version=structured.schema_version if structured else "structured-v2",
                        source_size_bytes=document.metadata.get("size_bytes"),
                        page_count=len(structured.pages) if structured else None,
                        language=structured.language if structured else None,
                        is_current=True,
                    )
                )
            for chunk in chunks:
                for index, element_id in enumerate(chunk.element_ids):
                    source_anchor_id = anchor_id(version_id, element_id)
                    if await session.get(SourceAnchorRecord, source_anchor_id) is not None:
                        continue
                    bbox = chunk.bbox_refs[index] if index < len(chunk.bbox_refs) else None
                    session.add(
                        SourceAnchorRecord(
                            anchor_id=source_anchor_id,
                            version_id=version_id,
                            element_id=element_id,
                            chunk_id=chunk.chunk_id,
                            page_number=chunk.page,
                            char_start=chunk.char_start,
                            char_end=chunk.char_end,
                            bbox_json=list(bbox) if bbox else None,
                            text_hash=text_hash(chunk.text),
                            anchor_kind="observed",
                            extraction_method=chunk.extraction_method,
                            confidence=chunk.min_confidence,
                        )
                    )

    async def _latest_version_id(self, session, logical_document_id: str) -> str | None:
        """Return the prior current version before inserting a new one."""

        return await session.scalar(
            select(DocumentVersionRecord.version_id)
            .where(
                DocumentVersionRecord.logical_document_id == logical_document_id,
                DocumentVersionRecord.is_current.is_(True),
            )
            .limit(1)
        )

    async def list_documents(
        self, limit: int = 200, project_id: str = DEFAULT_PROJECT_ID
    ) -> list[DocumentSummary]:
        """Return recently ingested documents for the user-facing library."""

        async with self._session_manager.session() as session:
            rows = (
                await session.execute(
                    select(DocumentRecord, func.count(ChunkRecord.chunk_id))
                    .outerjoin(ChunkRecord)
                    .where(DocumentRecord.project_id == project_id)
                    .group_by(DocumentRecord.document_id)
                    .order_by(DocumentRecord.ingested_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            DocumentSummary(
                document_id=document.document_id,
                project_id=document.project_id,
                title=document.title,
                source_uri=document.source_uri,
                content_hash=document.content_hash,
                ingested_at=document.ingested_at,
                chunk_count=chunk_count,
                media_type=str(document.metadata_json.get("media_type"))
                if document.metadata_json.get("media_type")
                else None,
                size_bytes=int(document.metadata_json.get("size_bytes"))
                if document.metadata_json.get("size_bytes") is not None
                else None,
            )
            for document, chunk_count in rows
        ]

    async def list_versions(
        self,
        logical_document_id: str,
        limit: int = 100,
    ) -> list[DocumentVersionSummary]:
        """Return immutable versions ordered from newest to oldest."""

        async with self._session_manager.session() as session:
            records = (
                await session.scalars(
                    select(DocumentVersionRecord)
                    .where(DocumentVersionRecord.logical_document_id == logical_document_id)
                    .order_by(DocumentVersionRecord.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            DocumentVersionSummary(
                version_id=record.version_id,
                logical_document_id=record.logical_document_id,
                document_id=record.document_id,
                predecessor_version_id=record.predecessor_version_id,
                content_hash=record.content_hash,
                version_label=record.version_label,
                effective_at=record.effective_at,
                superseded_at=record.superseded_at,
                is_current=record.is_current,
                parser_name=record.parser_name,
                parser_version=record.parser_version,
                schema_version=record.schema_version,
                page_count=record.page_count,
                language=record.language,
                created_at=record.created_at,
            )
            for record in records
        ]

    async def list_jobs(
        self, limit: int = 100, project_id: str = DEFAULT_PROJECT_ID
    ) -> list[JobStatusResponse]:
        """Return recent ingestion work for upload progress tracking."""

        async with self._session_manager.session() as session:
            records = (
                await session.scalars(
                    select(IngestionJobRecord)
                    .where(IngestionJobRecord.project_id == project_id)
                    .order_by(IngestionJobRecord.started_at.desc())
                    .limit(limit)
                )
            ).all()
        return [self._to_job_response(record) for record in records]

    def _to_job_response(self, record: IngestionJobRecord) -> JobStatusResponse:
        """Convert a job ORM record into the API response model."""

        return JobStatusResponse(
            job_id=record.job_id,
            project_id=record.project_id or DEFAULT_PROJECT_ID,
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
            attempt_count=record.attempt_count,
            max_attempts=record.max_attempts,
            stage=record.stage,
            progress_current=record.progress_current,
            progress_total=record.progress_total,
            lease_expires_at=record.lease_expires_at,
        )
