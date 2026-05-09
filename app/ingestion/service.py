"""End-to-end ingestion orchestration for corpus management."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.ingestion.chunker import SlidingWindowChunker
from app.ingestion.embedder import BaseEmbedder
from app.ingestion.loaders import LocalCorpusLoader
from app.ingestion.normalizer import DocumentNormalizer
from app.ingestion.object_store import LocalObjectStore
from app.ingestion.repository import IngestionRepository
from app.ingestion.schemas import IngestionMetrics, JobStatusResponse, SearchResult
from app.ingestion.sparse_index import SparseIndex
from app.ingestion.vector_writers import PgVectorWriter, QdrantWriter


class IngestionService:
    """Coordinate loading, normalization, chunking, embedding, and persistence."""

    def __init__(
        self,
        settings: Settings,
        repository: IngestionRepository,
        loader: LocalCorpusLoader,
        normalizer: DocumentNormalizer,
        chunker: SlidingWindowChunker,
        embedder: BaseEmbedder,
        object_store: LocalObjectStore,
        sparse_index: SparseIndex,
        pgvector_writer: PgVectorWriter,
        qdrant_writer: QdrantWriter,
    ) -> None:
        """Store dependencies required to run ingestion jobs end to end."""

        self._settings = settings
        self._repository = repository
        self._loader = loader
        self._normalizer = normalizer
        self._chunker = chunker
        self._embedder = embedder
        self._object_store = object_store
        self._sparse_index = sparse_index
        self._pgvector_writer = pgvector_writer
        self._qdrant_writer = qdrant_writer

    async def initialize(self) -> None:
        """Initialize storage backends that the ingestion service depends on."""

        await self._object_store.initialize()
        await self._sparse_index.initialize()
        await self._pgvector_writer.initialize()

    async def ingest_path(
        self,
        source_path: Path,
        force_reindex: bool = False,
        embedding_version: str = "v1",
        index_version: str = "v1",
    ) -> JobStatusResponse:
        """Ingest a file or directory and return the resulting job summary."""

        job_id = str(uuid4())
        await self._repository.create_job(
            job_id=job_id,
            source_path=str(source_path),
            force_reindex=force_reindex,
            embedding_version=embedding_version,
            index_version=index_version,
        )

        documents_seen = 0
        documents_indexed = 0
        documents_skipped = 0
        chunks_written = 0

        try:
            for loaded_document in self._loader.load(source_path):
                documents_seen += 1
                document = self._normalizer.normalize(loaded_document)
                existing_state = await self._repository.get_document_state(
                    document.source_uri
                )
                if (
                    existing_state
                    and existing_state.content_hash == document.content_hash
                    and not force_reindex
                ):
                    documents_skipped += 1
                    continue

                chunks = self._chunker.chunk(
                    document=document,
                    embedding_model=self._settings.embedding_model,
                    embedding_version=embedding_version,
                    index_version=index_version,
                )
                embeddings = await self._embedder.embed_texts(
                    [chunk.text for chunk in chunks]
                )
                raw_text_path = await self._object_store.store_document(
                    document.document_id,
                    document.text,
                )
                await self._repository.save_document(document, chunks, raw_text_path)
                await self._pgvector_writer.upsert_chunks(document, chunks, embeddings)
                await self._qdrant_writer.upsert_chunks(document, chunks, embeddings)
                await self._sparse_index.replace_document_chunks(document, chunks)
                documents_indexed += 1
                chunks_written += len(chunks)
        except Exception as error:
            await self._repository.fail_job(
                job_id=job_id,
                documents_seen=documents_seen,
                documents_indexed=documents_indexed,
                documents_skipped=documents_skipped,
                chunks_written=chunks_written,
                error_message=str(error),
            )
            raise

        await self._repository.complete_job(
            job_id=job_id,
            documents_seen=documents_seen,
            documents_indexed=documents_indexed,
            documents_skipped=documents_skipped,
            chunks_written=chunks_written,
        )
        job = await self._repository.get_job(job_id)
        if job is None:
            msg = f"Ingestion job disappeared before completion: {job_id}"
            raise RuntimeError(msg)
        return job

    async def reindex_path(
        self,
        source_path: Path,
        embedding_version: str = "v1",
        index_version: str = "v1",
    ) -> JobStatusResponse:
        """Force a re-index of the given corpus source path."""

        return await self.ingest_path(
            source_path=source_path,
            force_reindex=True,
            embedding_version=embedding_version,
            index_version=index_version,
        )

    async def get_job(self, job_id: str) -> JobStatusResponse | None:
        """Return the persisted ingestion job state for the given identifier."""

        return await self._repository.get_job(job_id)

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Run a local sparse search over ingested chunks for validation."""

        return await self._sparse_index.search(query=query, top_k=top_k)

    async def metrics(self) -> IngestionMetrics:
        """Return aggregate ingestion counts for observability and dashboards."""

        return await self._repository.metrics()
