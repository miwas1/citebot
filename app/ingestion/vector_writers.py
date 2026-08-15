"""Writer for the PostgreSQL pgvector embedding index."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from app.db.session import DatabaseSessionManager
from app.ingestion.schemas import CanonicalDocument, ChunkPayload


class PgVectorWriter:
    """Write chunk embeddings into a pgvector-backed PostgreSQL table."""

    def __init__(
        self,
        session_manager: DatabaseSessionManager,
        vector_size: int,
    ) -> None:
        """Store the shared database session manager and vector settings."""

        self._session_manager = session_manager
        self._vector_size = vector_size

    async def initialize(self) -> None:
        """Create the pgvector extension and embedding table when enabled."""

        if not self._session_manager.is_postgresql:
            return
        async with self._session_manager.session() as session:
            # Avoid concurrent extension/table/index DDL from API and worker startup.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(734867320240815002)")
            )
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
                        PRIMARY KEY (chunk_id, embedding_version, index_version),
                        FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
                            ON DELETE CASCADE,
                        FOREIGN KEY (document_id) REFERENCES documents(document_id)
                            ON DELETE CASCADE
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS chunk_embeddings_vector_hnsw_idx
                    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS chunk_embeddings_filter_idx
                    ON chunk_embeddings (embedding_version, index_version, document_id)
                    """
                )
            )
            stored_type = await session.scalar(
                text(
                    """
                    SELECT format_type(attribute.atttypid, attribute.atttypmod)
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS relation ON relation.oid = attribute.attrelid
                    WHERE relation.relname = 'chunk_embeddings'
                      AND attribute.attname = 'embedding'
                      AND attribute.attnum > 0
                    """
                )
            )
            expected_type = f"vector({self._vector_size})"
            if stored_type != expected_type:
                raise RuntimeError(
                    "pgvector dimension mismatch: "
                    f"database has {stored_type}, configuration requires {expected_type}"
                )

    async def upsert_chunks(
        self,
        _document: CanonicalDocument,
        chunks: Sequence[ChunkPayload],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Upsert embeddings for the provided chunks into PostgreSQL."""

        if not self._session_manager.is_postgresql or not chunks:
            return
        async with self._session_manager.session() as session:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                if len(embedding) != self._vector_size:
                    raise ValueError(
                        f"Embedding for {chunk.chunk_id} has {len(embedding)} values; "
                        f"expected {self._vector_size}"
                    )
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
