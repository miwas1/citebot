"""Writers for pgvector and Qdrant embedding indexes."""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from sqlalchemy import text

from app.db.session import DatabaseSessionManager
from app.ingestion.schemas import CanonicalDocument, ChunkPayload


class PgVectorWriter:
    """Write chunk embeddings into a pgvector-backed PostgreSQL table."""

    def __init__(
        self,
        session_manager: DatabaseSessionManager,
        enabled: bool,
        vector_size: int,
    ) -> None:
        """Store the shared database session manager and vector settings."""

        self._session_manager = session_manager
        self._enabled = enabled
        self._vector_size = vector_size

    async def initialize(self) -> None:
        """Create the pgvector extension and embedding table when enabled."""

        if not self._enabled:
            return
        async with self._session_manager.session() as session:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await session.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS chunk_embeddings (
                        chunk_id TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        embedding_version TEXT NOT NULL,
                        index_version TEXT NOT NULL,
                        embedding VECTOR({self._vector_size}) NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (chunk_id, embedding_version, index_version)
                    )
                    """
                )
            )

    async def upsert_chunks(
        self,
        _document: CanonicalDocument,
        chunks: Sequence[ChunkPayload],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Upsert embeddings for the provided chunks into PostgreSQL."""

        if not self._enabled or not chunks:
            return
        async with self._session_manager.session() as session:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                embedding_literal = (
                    "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
                )
                await session.execute(
                    text(
                        """
                        INSERT INTO chunk_embeddings (
                            chunk_id,
                            document_id,
                            embedding_model,
                            embedding_version,
                            index_version,
                            embedding
                        ) VALUES (
                            :chunk_id,
                            :document_id,
                            :embedding_model,
                            :embedding_version,
                            :index_version,
                            CAST(:embedding AS vector)
                        )
                        ON CONFLICT (chunk_id, embedding_version, index_version)
                        DO UPDATE SET
                            document_id = EXCLUDED.document_id,
                            embedding_model = EXCLUDED.embedding_model,
                            embedding = EXCLUDED.embedding,
                            updated_at = NOW()
                        """
                    ),
                    {
                        "chunk_id": chunk.chunk_id,
                        "document_id": chunk.document_id,
                        "embedding_model": chunk.embedding_model,
                        "embedding_version": chunk.embedding_version,
                        "index_version": chunk.index_version,
                        "embedding": embedding_literal,
                    },
                )


class QdrantWriter:
    """Write chunk embeddings into a Qdrant collection via HTTP."""

    def __init__(self, base_url: str, collection_name: str, enabled: bool) -> None:
        """Store the Qdrant endpoint, collection, and enablement flag."""

        self._base_url = base_url.rstrip("/")
        self._collection_name = collection_name
        self._enabled = enabled

    async def ping(self) -> bool:
        """Check whether the Qdrant server responds to a collection listing request."""

        if not self._enabled:
            return True
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/collections")
                response.raise_for_status()
        except Exception:
            return False
        return True

    async def ensure_collection(self, vector_size: int) -> None:
        """Create the target Qdrant collection when it does not yet exist."""

        if not self._enabled:
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self._base_url}/collections/{self._collection_name}"
            )
            if response.status_code == 200:
                return
            create_response = await client.put(
                f"{self._base_url}/collections/{self._collection_name}",
                json={"vectors": {"size": vector_size, "distance": "Cosine"}},
            )
            create_response.raise_for_status()

    async def upsert_chunks(
        self,
        document: CanonicalDocument,
        chunks: Sequence[ChunkPayload],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Upsert chunk embeddings and payload metadata into Qdrant."""

        if not self._enabled or not chunks:
            return
        await self.ensure_collection(len(embeddings[0]))
        points = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            points.append(
                {
                    "id": chunk.chunk_id,
                    "vector": list(embedding),
                    "payload": {
                        "document_id": chunk.document_id,
                        "source_uri": document.source_uri,
                        "title": document.title,
                        "location_marker": chunk.location_marker,
                        "text": chunk.text,
                        "embedding_model": chunk.embedding_model,
                        "embedding_version": chunk.embedding_version,
                        "index_version": chunk.index_version,
                    },
                }
            )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{self._base_url}/collections/{self._collection_name}/points?wait=true",
                json={"points": points},
            )
            response.raise_for_status()
