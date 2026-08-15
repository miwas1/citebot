"""Focused tests for evidence, deterministic calculations, and version diffs."""


import asyncio
from datetime import UTC, datetime

import pytest

from app.agents.generation import LocalAnswerGenerator
from app.agents.schemas import (
    Citation,
    ResearchAnswer,
    ResearchContext,
    ResearchQueryRequest,
    ResearchResponse,
)
from app.agents.service import ResearchAgentService
from app.calculation.schemas import CalculationCell, CalculationPlan
from app.calculation.service import CalculationService
from app.core.config import Settings
from app.db.session import DatabaseSessionManager
from app.diffing.schemas import ElementSnapshot
from app.diffing.service import DocumentDiffService
from app.evidence.service import EvidenceService
from app.ingestion.chunker import SlidingWindowChunker
from app.ingestion.schemas import (
    CanonicalDocument,
    DocumentElement,
    DocumentTable,
    SearchResult,
    StructuredDocument,
    StructuredPage,
)
from app.tools.citation_verifier import CitationVerifier
from app.workflows.checkpointer import DatabaseCheckpointSaver
from app.workflows.review import transition
from app.workflows.review_gate import ReviewGate
from app.workflows.schemas import WorkProduct
from app.workflows.service import WorkflowService


@pytest.mark.asyncio
async def test_citation_verifier_detects_numeric_mismatch() -> None:
    """A cited passage cannot support a changed numeric claim."""

    context = ResearchContext(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Report",
        source_uri="file:///report.pdf",
        text="The contract term is 12 months.",
        score=1.0,
        source_backend="test",
    )
    answer = ResearchAnswer(
        direct_answer="The contract term is 24 months.",
        citations=[
            Citation(
                citation_id="C1",
                chunk_id="chunk-1",
                document_id="doc-1",
                title="Report",
                source_uri="file:///report.pdf",
                support_span="The contract term is 12 months.",
                quoted_support="The contract term is 12 months.",
            )
        ],
    )
    result = await CitationVerifier().verify(answer, [context])
    assert result.overall_verdict == "unsupported"
    assert result.contradiction_count == 1
    assert result.claims[0].verdict == "contradicted"


def test_calculation_service_is_deterministic_and_requires_anchors() -> None:
    """Typed calculations preserve units, warnings, and reproducibility."""

    result = CalculationService().execute(
        CalculationPlan(operation="percentage_change", input_names=["old", "new"]),
        [
            CalculationCell(name="old", value=100, unit="USD", currency="USD", anchor_ids=["a"]),
            CalculationCell(name="new", value=125, unit="USD", currency="USD", anchor_ids=["b"]),
        ],
    )
    assert result.value == 25
    assert result.warnings == []
    assert len(result.reproducibility_hash) == 64


def test_document_diff_preserves_old_and_new_anchors() -> None:
    """Modified elements retain both sides of the evidence chain."""

    result = DocumentDiffService().compare(
        "old-v1",
        "new-v2",
        [
            ElementSnapshot(
                element_id="old",
                element_type="paragraph",
                text="Term is 12 months.",
                anchor_id="a",
            )
        ],
        [
            ElementSnapshot(
                element_id="new",
                element_type="paragraph",
                text="Term is 24 months.",
                anchor_id="b",
            )
        ],
    )
    assert result.elements[0].operation == "modified"
    assert result.elements[0].old_anchor_id == "a"
    assert result.elements[0].new_anchor_id == "b"


def test_contract_workflow_marks_product_for_review() -> None:
    """High-stakes workflow packs remain review-gated even when claims pass."""

    response = ResearchResponse(
        session_id="s",
        trace_id="t",
        answer=ResearchAnswer(direct_answer="The term is 12 months."),
        verification={"overall_verdict": "supported", "claims": []},
        memory={},
    )
    # A product with no claims is still a draft that requires explicit review.
    product = WorkflowService().build_product("contract_review", response)
    assert product.status == "needs_review"
    assert product.workflow_id == "contract_review"


def test_evidence_pipeline_extracts_and_flags_numeric_claims() -> None:
    """The explicit low-resource pipeline detects a changed number before NLI."""

    answer = ResearchAnswer(
        direct_answer="The term is 24 months.",
        citations=[
            Citation(
                citation_id="C1",
                chunk_id="chunk-1",
                document_id="doc-1",
                title="Report",
                source_uri="file:///report.pdf",
                support_span="The term is 12 months.",
                quoted_support="The term is 12 months.",
            )
        ],
    )
    context = ResearchContext(
        chunk_id="chunk-1",
        document_id="doc-1",
        title="Report",
        source_uri="file:///report.pdf",
        text="The term is 12 months.",
        score=1.0,
        source_backend="test",
    )
    service = EvidenceService()
    claims = service.extract(answer)
    decisions = service.verify(claims, service.select(claims, answer, [context]))
    assert claims[0].claim_type == "numeric"
    assert decisions[0].verdict == "contradicted"


def test_structured_table_rows_are_indexable() -> None:
    """Structured tables produce row-level chunks with stable cell anchors."""

    document = CanonicalDocument(
        document_id="doc-1",
        source_uri="file:///report.pdf",
        title="Report",
        text="Table t1\n| Vendor | Price |\n| --- | --- |\n| Acme | 10 |",
        content_hash="version-1",
        ingested_at=datetime.now(UTC),
        structured=StructuredDocument(
            pages=[
                StructuredPage(
                    page_number=1,
                    elements=[
                        DocumentElement(
                            element_id="t1-r1-c0",
                            element_type="table_cell",
                            text="Acme",
                            source_engine="test",
                            table_id="t1",
                            row_index=1,
                            column_index=0,
                        ),
                        DocumentElement(
                            element_id="t1-r1-c1",
                            element_type="table_cell",
                            text="10",
                            source_engine="test",
                            table_id="t1",
                            row_index=1,
                            column_index=1,
                        ),
                    ],
                )
            ],
            tables=[
                DocumentTable(
                    table_id="t1",
                    headers=["Vendor", "Price"],
                    rows=[["Acme", "10"]],
                    source_element_ids=["t1-r1-c0", "t1-r1-c1"],
                )
            ],
        ),
    )
    chunks = SlidingWindowChunker(50, 5).chunk(document, "test", "v1", "i1")
    rows = [chunk for chunk in chunks if chunk.chunk_level == "row"]
    assert len(rows) == 1
    assert rows[0].source_anchor_ids == ["version-1:t1-r1-c0", "version-1:t1-r1-c1"]


def test_review_state_machine_rejects_approval_after_rejection() -> None:
    """Review transitions fail closed instead of allowing state resurrection."""

    assert transition("needs_review", "approve").after == "approved"
    with pytest.raises(ValueError):
        transition("rejected", "approve")


@pytest.mark.asyncio
async def test_research_agent_completes_full_evidence_graph() -> None:
    """The full graph must complete with deterministic local dependencies."""

    class Retrieval:
        async def explain(self, request):
            return [
                SearchResult(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    title="Report",
                    source_uri="file:///report.pdf",
                    score=1.0,
                    text="The contract term is 12 months.",
                )
            ]

    class Web:
        async def search(self, query, trace_id):
            raise AssertionError("web search should not run")

    class Sandbox:
        async def execute(self, execution):
            raise AssertionError("python analysis should not run")

    class Store:
        def __init__(self):
            self.record = None

        async def get(self, session_id):
            return self.record

        async def save(self, record):
            self.record = record

    settings = Settings(
        runtime_mode="offline",
        database_url="postgresql+asyncpg://citebot:citebot@postgres:5432/citebot",
        answer_provider="local",
        research_min_context_score=0.0,
    )
    store = Store()
    service = ResearchAgentService(
        settings,
        Retrieval(),
        LocalAnswerGenerator(),
        Web(),
        Sandbox(),
        CitationVerifier(),
        store,
    )
    response = await asyncio.wait_for(
        service.answer(ResearchQueryRequest(query="What is the term?")),
        timeout=3,
    )
    assert response.error is None
    assert response.answer.answer_status in {"supported", "qualified"}
    assert "citation_verification" in response.state_transitions
    assert response.stage_timings_ms


@pytest.mark.asyncio
async def test_review_gate_checkpoint_survives_lookup_and_resume(tmp_path) -> None:
    """LangGraph review interrupts resume from the primary database."""

    manager = DatabaseSessionManager(
        f"sqlite+aiosqlite:///{tmp_path / 'primary.db'}"
    )
    await manager.initialize()
    saver = DatabaseCheckpointSaver(manager)
    gate = ReviewGate(saver)
    product = WorkProduct(
        work_product_id="wp",
        analysis_run_id="run-1",
        workflow_id="contract_review",
        workflow_version="v1",
        schema_version="v1",
        title="Contract review",
        status="needs_review",
    )
    interrupted = await gate.start(product)
    assert "__interrupt__" in interrupted
    assert (
        await saver.aget_tuple({"configurable": {"thread_id": "run-1"}})
        is not None
    )
    resumed = await gate.resume("run-1", "approve", "Reviewed")
    assert resumed["decision"] == "approve"
    await manager.close()
