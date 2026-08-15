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
from app.ingestion.schemas import (
    DEFAULT_PROJECT_ID,
    DocumentSummary,
    DocumentVersionSummary,
    IngestionMetrics,
    JobStatusResponse,
)
from app.ingestion.vector_writers import PgVectorWriter


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
        pgvector_writer: PgVectorWriter,
    ) -> None:
        """Store dependencies required to run ingestion jobs end to end."""

        self._settings = settings
        self._repository = repository
        self._loader = loader
        self._normalizer = normalizer
        self._chunker = chunker
        self._embedder = embedder
        self._object_store = object_store
        self._pgvector_writer = pgvector_writer

    async def initialize(self) -> None:
        """Initialize storage backends that the ingestion service depends on."""

        await self._object_store.initialize()
        self._settings.structured_document_path.mkdir(parents=True, exist_ok=True)
        await self._pgvector_writer.initialize()

    async def ingest_path(
        self,
        source_path: Path,
        force_reindex: bool = False,
        embedding_version: str = "bge-small-en-v1.5",
        index_version: str = "v2",
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> JobStatusResponse:
        """Ingest a file or directory and return the resulting job summary."""

        job_id = str(uuid4())
        await self._repository.create_job(
            job_id=job_id,
            project_id=project_id,
            source_path=str(source_path),
            force_reindex=force_reindex,
            embedding_version=embedding_version,
            index_version=index_version,
            status="running",
            max_attempts=self._settings.ingestion_max_attempts,
        )
        return await self._process_job(
            job_id=job_id,
            source_path=source_path,
            project_id=project_id,
            force_reindex=force_reindex,
            embedding_version=embedding_version,
            index_version=index_version,
        )

    async def enqueue_path(
        self,
        source_path: Path,
        force_reindex: bool = False,
        embedding_version: str | None = None,
        index_version: str = "v2",
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> JobStatusResponse:
        """Persist an ingestion request for the durable worker queue."""

        job_id = str(uuid4())
        await self._repository.create_job(
            job_id=job_id,
            project_id=project_id,
            source_path=str(source_path),
            force_reindex=force_reindex,
            embedding_version=embedding_version or self._settings.embedding_version,
            index_version=index_version,
            status="queued",
            max_attempts=self._settings.ingestion_max_attempts,
        )
        job = await self._repository.get_job(job_id)
        if job is None:
            raise RuntimeError(f"Ingestion job disappeared after enqueue: {job_id}")
        return job

    async def run_next_job(self, worker_id: str) -> JobStatusResponse | None:
        """Claim and process one queued job, returning its terminal status."""

        job = await self._repository.claim_next_job(
            worker_id=worker_id,
            lease_seconds=self._settings.queue_lease_seconds,
        )
        if job is None:
            return None
        return await self._process_job(
            job_id=job.job_id,
            source_path=Path(job.source_path),
            force_reindex=job.force_reindex,
            embedding_version=job.embedding_version,
            index_version=job.index_version,
            project_id=job.project_id,
            worker_id=worker_id,
        )

    async def recover_stale_jobs(self) -> int:
        """Return expired worker leases to the durable queue."""

        return await self._repository.recover_stale_jobs()

    async def list_documents(
        self, limit: int = 200, project_id: str = DEFAULT_PROJECT_ID
    ) -> list[DocumentSummary]:
        """List documents available to the user workspace."""

        return await self._repository.list_documents(limit=limit, project_id=project_id)

    async def list_jobs(
        self, limit: int = 100, project_id: str = DEFAULT_PROJECT_ID
    ) -> list[JobStatusResponse]:
        """List recent ingestion jobs for progress tracking."""

        return await self._repository.list_jobs(limit=limit, project_id=project_id)

    async def list_versions(self, logical_document_id: str) -> list[DocumentVersionSummary]:
        """Expose immutable document revisions to API and workflow callers."""

        return await self._repository.list_versions(logical_document_id)

    async def _process_job(
        self,
        job_id: str,
        source_path: Path,
        force_reindex: bool,
        embedding_version: str,
        index_version: str,
        worker_id: str | None = None,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> JobStatusResponse:
        """Process a foreground or worker-claimed job using the same pipeline."""

        documents_seen = 0
        documents_indexed = 0
        documents_skipped = 0
        chunks_written = 0

        try:
            for loaded_document in self._loader.iter_load(source_path):
                documents_seen += 1
                if worker_id is not None:
                    await self._repository.heartbeat(
                        job_id,
                        worker_id,
                        self._settings.queue_lease_seconds,
                    )
                document = self._normalizer.normalize(loaded_document, project_id)
                existing_state = await self._repository.get_document_state(
                    project_id, document.source_uri
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
                if document.structured is not None:
                    structured_path = await self._object_store.store_structured(
                        document.document_id,
                        document.structured.model_dump(mode="json"),
                        self._settings.structured_document_path,
                        document.content_hash,
                    )
                    document = document.model_copy(
                        update={
                            "metadata": {
                                **document.metadata,
                                "structured_document_path": structured_path,
                                "structured_schema_version": document.structured.schema_version,
                            }
                        }
                    )
                await self._repository.save_document(document, chunks, raw_text_path)
                await self._repository.save_provenance(document, chunks)
                await self._pgvector_writer.upsert_chunks(document, chunks, embeddings)
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
        embedding_version: str = "bge-small-en-v1.5",
        index_version: str = "v2",
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> JobStatusResponse:
        """Force a re-index of the given corpus source path."""

        return await self.ingest_path(
            source_path=source_path,
            project_id=project_id,
            force_reindex=True,
            embedding_version=embedding_version,
            index_version=index_version,
        )

    async def get_job(self, job_id: str) -> JobStatusResponse | None:
        """Return the persisted ingestion job state for the given identifier."""

        return await self._repository.get_job(job_id)

    async def metrics(self) -> IngestionMetrics:
        """Return aggregate ingestion counts for observability and dashboards."""

        return await self._repository.metrics()
