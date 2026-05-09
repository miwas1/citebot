"""Application lifecycle management and service wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.health import HealthService
from app.db.session import DatabaseSessionManager
from app.ingestion.chunker import SlidingWindowChunker
from app.ingestion.embedder import build_embedder
from app.ingestion.loaders import LocalCorpusLoader
from app.ingestion.normalizer import DocumentNormalizer
from app.ingestion.object_store import LocalObjectStore
from app.ingestion.repository import IngestionRepository
from app.ingestion.service import IngestionService
from app.ingestion.sparse_index import SparseIndex
from app.ingestion.vector_writers import PgVectorWriter, QdrantWriter
from app.retrieval.repository import RetrievalRepository
from app.retrieval.reranker import build_reranker
from app.retrieval.service import RetrievalService


@dataclass(slots=True)
class ServiceContainer:
    """Runtime container for app services and their shared dependencies."""

    settings: Settings
    session_manager: DatabaseSessionManager
    health_service: HealthService
    ingestion_service: IngestionService
    retrieval_service: RetrievalService

    async def initialize(self) -> None:
        """Initialize storage, tables, and external writer schemas."""

        await self.session_manager.initialize()
        await self.ingestion_service.initialize()

    async def close(self) -> None:
        """Release runtime resources during application shutdown."""

        await self.session_manager.close()


def build_container(settings: Settings) -> ServiceContainer:
    """Construct the service graph for the current process."""

    session_manager = DatabaseSessionManager(settings.database_url)
    repository = IngestionRepository(session_manager)
    loader = LocalCorpusLoader()
    normalizer = DocumentNormalizer()
    chunker = SlidingWindowChunker(settings.chunk_size, settings.chunk_overlap)
    embedder = build_embedder(settings)
    object_store = LocalObjectStore(settings.object_storage_path)
    sparse_index = SparseIndex(settings.sparse_index_path)
    pgvector_writer = PgVectorWriter(
        session_manager=session_manager,
        enabled=settings.enable_pgvector,
        vector_size=settings.embedding_dimension,
    )
    qdrant_writer = QdrantWriter(
        base_url=settings.qdrant_url,
        collection_name=settings.qdrant_collection,
        enabled=settings.enable_qdrant,
    )
    retrieval_repository = RetrievalRepository(session_manager)
    reranker = build_reranker(settings)
    retrieval_service = RetrievalService(
        settings=settings,
        session_manager=session_manager,
        repository=retrieval_repository,
        embedder=embedder,
        sparse_index=sparse_index,
        reranker=reranker,
    )
    ingestion_service = IngestionService(
        settings=settings,
        repository=repository,
        loader=loader,
        normalizer=normalizer,
        chunker=chunker,
        embedder=embedder,
        object_store=object_store,
        sparse_index=sparse_index,
        pgvector_writer=pgvector_writer,
        qdrant_writer=qdrant_writer,
    )
    health_service = HealthService(settings, session_manager, qdrant_writer)
    return ServiceContainer(
        settings=settings,
        session_manager=session_manager,
        health_service=health_service,
        ingestion_service=ingestion_service,
        retrieval_service=retrieval_service,
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize the service container for the FastAPI application lifespan."""

    settings = get_settings()
    container = build_container(settings)
    await container.initialize()
    application.state.container = container
    try:
        yield
    finally:
        await container.close()
