"""Application lifecycle management and service wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI

from app.agents.generation import build_answer_generator
from app.agents.service import ResearchAgentService
from app.agents.session_store import ResearchSessionStore
from app.calculation.repository import CalculationRepository
from app.calculation.service import CalculationService
from app.core.admission import BoundedAdmission
from app.core.config import Settings, get_settings
from app.core.health import HealthService
from app.core.model_manifest import verify_model_manifest
from app.db.session import DatabaseSessionManager
from app.diffing.repository import DiffRepository
from app.diffing.service import DocumentDiffService
from app.evaluation.service import EvaluationService
from app.evidence.nli import CompactNliVerifier
from app.evidence.repository import EvidenceLedgerRepository
from app.evidence.service import EvidenceService
from app.ingestion.bootstrap import ensure_sample_corpus
from app.ingestion.chunker import SlidingWindowChunker
from app.ingestion.embedder import build_embedder
from app.ingestion.loaders import LocalCorpusLoader
from app.ingestion.normalizer import DocumentNormalizer
from app.ingestion.object_store import LocalObjectStore
from app.ingestion.repository import IngestionRepository
from app.ingestion.service import IngestionService
from app.ingestion.vector_writers import PgVectorWriter
from app.observability.metrics import InMemoryMetricsRegistry
from app.projects.repository import ProjectRepository
from app.projects.service import ProjectService
from app.retrieval.repository import RetrievalRepository
from app.retrieval.reranker import build_reranker
from app.retrieval.service import RetrievalService
from app.tools.citation_verifier import CitationVerifier
from app.tools.python_sandbox import PythonSandboxTool
from app.tools.web_search import build_web_search_tool
from app.workflows.checkpointer import DatabaseCheckpointSaver
from app.workflows.repository import WorkflowRepository
from app.workflows.review_gate import ReviewGate
from app.workflows.service import WorkflowService


@dataclass(slots=True)
class ServiceContainer:
    """Runtime container for app services and their shared dependencies."""

    settings: Settings
    session_manager: DatabaseSessionManager
    health_service: HealthService
    ingestion_repository: IngestionRepository
    project_repository: ProjectRepository
    project_service: ProjectService
    ingestion_service: IngestionService
    retrieval_service: RetrievalService
    research_agent_service: ResearchAgentService
    research_session_store: ResearchSessionStore
    evaluation_service: EvaluationService
    research_admission: BoundedAdmission
    workflow_service: WorkflowService
    workflow_repository: WorkflowRepository
    calculation_service: CalculationService
    diff_service: DocumentDiffService
    evidence_repository: EvidenceLedgerRepository
    calculation_repository: CalculationRepository
    diff_repository: DiffRepository
    review_gate: ReviewGate

    async def initialize(self) -> None:
        """Initialize storage, tables, and external writer schemas."""

        if self.settings.runtime_mode == "offline":
            verify_model_manifest(self.settings.model_manifest_path)
        await self.session_manager.initialize()
        await self.project_service.ensure_sample_project()
        await self.ingestion_service.initialize()
        await ensure_sample_corpus(
            self.settings,
            self.ingestion_repository,
            self.ingestion_service,
        )

    async def close(self) -> None:
        """Release runtime resources during application shutdown."""

        await self.session_manager.close()


def build_container(
    settings: Settings,
    metrics_registry: InMemoryMetricsRegistry | None = None,
) -> ServiceContainer:
    """Construct the service graph for the current process."""

    session_manager = DatabaseSessionManager(settings.database_url)
    repository = IngestionRepository(session_manager)
    project_repository = ProjectRepository(session_manager)
    project_service = ProjectService(project_repository)
    loader = LocalCorpusLoader(settings)
    normalizer = DocumentNormalizer()
    chunker = SlidingWindowChunker(settings.chunk_size, settings.chunk_overlap)
    embedder = build_embedder(settings)
    object_store = LocalObjectStore(settings.object_storage_path)
    pgvector_writer = PgVectorWriter(
        session_manager=session_manager,
        vector_size=settings.embedding_dimension,
    )
    retrieval_repository = RetrievalRepository(session_manager)
    reranker = build_reranker(settings)
    answer_generator = build_answer_generator(settings, metrics_registry=metrics_registry)
    web_search_tool = build_web_search_tool(settings)
    python_sandbox = PythonSandboxTool(settings)
    nli_verifier = (
        CompactNliVerifier(
            settings.verification_model,
            max_pairs=settings.verification_max_pairs,
        )
        if settings.verification_provider == "sentence-transformers"
        else None
    )
    citation_verifier = CitationVerifier(
        nli_verifier=nli_verifier,
        max_nli_pairs=settings.verification_max_pairs,
    )
    session_store = ResearchSessionStore(session_manager)
    evidence_repository = EvidenceLedgerRepository(session_manager)
    retrieval_service = RetrievalService(
        settings=settings,
        session_manager=session_manager,
        repository=retrieval_repository,
        embedder=embedder,
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
        pgvector_writer=pgvector_writer,
    )
    research_agent_service = ResearchAgentService(
        settings=settings,
        retrieval_service=retrieval_service,
        answer_generator=answer_generator,
        web_search_tool=web_search_tool,
        python_sandbox=python_sandbox,
        citation_verifier=citation_verifier,
        session_store=session_store,
        evidence_repository=evidence_repository,
        evidence_service=EvidenceService(),
    )
    evaluation_service = EvaluationService(
        settings=settings,
        ingestion_service=ingestion_service,
        research_agent_service=research_agent_service,
    )
    health_service = HealthService(settings, session_manager)
    workflow_repository = WorkflowRepository(session_manager)
    calculation_repository = CalculationRepository(session_manager)
    diff_repository = DiffRepository(session_manager)
    review_gate = ReviewGate(DatabaseCheckpointSaver(session_manager))
    return ServiceContainer(
        settings=settings,
        session_manager=session_manager,
        health_service=health_service,
        ingestion_repository=repository,
        project_repository=project_repository,
        project_service=project_service,
        ingestion_service=ingestion_service,
        retrieval_service=retrieval_service,
        research_agent_service=research_agent_service,
        research_session_store=session_store,
        evaluation_service=evaluation_service,
        research_admission=BoundedAdmission(
            concurrency=settings.research_concurrency,
            queue_size=settings.research_queue_size,
            timeout_seconds=settings.research_queue_timeout_seconds,
        ),
        workflow_service=WorkflowService(),
        workflow_repository=workflow_repository,
        calculation_service=CalculationService(),
        diff_service=DocumentDiffService(),
        evidence_repository=evidence_repository,
        calculation_repository=calculation_repository,
        diff_repository=diff_repository,
        review_gate=review_gate,
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize the service container for the FastAPI application lifespan."""

    settings = get_settings()
    container = build_container(
        settings,
        metrics_registry=getattr(application.state, "metrics_registry", None),
    )
    await container.initialize()
    application.state.container = container
    try:
        yield
    finally:
        await container.close()
