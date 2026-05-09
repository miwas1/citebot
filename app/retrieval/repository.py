"""Repository helpers for retrieval-time chunk loading and filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db.models import ChunkRecord, DocumentRecord
from app.db.session import DatabaseSessionManager
from app.ingestion.schemas import RetrievalFilters


@dataclass(slots=True)
class IndexedChunkRecord:
    """Combined chunk and document metadata needed during retrieval."""

    chunk_id: str
    document_id: str
    title: str
    source_uri: str
    location_marker: str | None
    text: str
    access_policy: str
    embedding_version: str
    index_version: str
    section: str | None
    page: int | None
    document_metadata: dict[str, Any]


class RetrievalRepository:
    """Load chunk payloads from persisted metadata tables for retrieval operations."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        """Store the shared database session manager."""

        self._session_manager = session_manager

    async def list_chunks(
        self,
        filters: RetrievalFilters | None = None,
    ) -> list[IndexedChunkRecord]:
        """Return persisted chunks that satisfy the requested retrieval filters."""

        statement = select(ChunkRecord, DocumentRecord).join(DocumentRecord)
        if filters is not None:
            if filters.document_ids:
                statement = statement.where(
                    ChunkRecord.document_id.in_(filters.document_ids)
                )
            if filters.source_uris:
                statement = statement.where(
                    DocumentRecord.source_uri.in_(filters.source_uris)
                )
            if filters.access_policies:
                statement = statement.where(
                    DocumentRecord.access_policy.in_(filters.access_policies)
                )
            if filters.embedding_version is not None:
                statement = statement.where(
                    ChunkRecord.embedding_version == filters.embedding_version
                )
            if filters.index_version is not None:
                statement = statement.where(
                    ChunkRecord.index_version == filters.index_version
                )
        async with self._session_manager.session() as session:
            rows = (await session.execute(statement)).all()
        return [self._to_indexed_chunk(chunk, document) for chunk, document in rows]

    def _to_indexed_chunk(
        self,
        chunk: ChunkRecord,
        document: DocumentRecord,
    ) -> IndexedChunkRecord:
        """Convert ORM records into the flatter retrieval payload shape."""

        return IndexedChunkRecord(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            title=document.title,
            source_uri=document.source_uri,
            location_marker=chunk.location_marker,
            text=chunk.text,
            access_policy=document.access_policy,
            embedding_version=chunk.embedding_version,
            index_version=chunk.index_version,
            section=chunk.section,
            page=chunk.page,
            document_metadata=dict(document.metadata_json or {}),
        )
