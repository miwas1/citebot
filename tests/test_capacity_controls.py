"""Regression tests for the 16 GB workstation capacity safeguards."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.admission import AdmissionRejected, BoundedAdmission
from app.core.config import Settings
from app.core.lifecycle import build_container
from app.ingestion.loaders import LocalCorpusLoader
from app.ingestion.schemas import CanonicalDocument, ChunkPayload, RetrievalFilters, SearchRequest


def test_16gb_defaults_are_conservative() -> None:
    """The base settings should start with the bounded local-runtime profile."""

    settings = Settings(_env_file=None)

    assert settings.llm_context_tokens == 4096
    assert settings.ocr_max_pages == 100
    assert settings.max_input_bytes == 25 * 1024 * 1024
    assert settings.embedding_batch_size == 4
    assert settings.research_rate_limit_requests == 30
    assert settings.admin_rate_limit_requests == 10
    assert settings.research_concurrency == 1
    assert settings.research_queue_size == 2


def test_loader_streams_jsonl_and_enforces_document_budget(tmp_path: Path) -> None:
    """JSONL records are yielded incrementally and job limits fail closed."""

    source = tmp_path / "papers.jsonl"
    source.write_text(
        "\n".join(
            [
            '{"title":"one","text":"first"}',
            '{"title":"two","text":"second"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    settings = Settings(
        INGESTION_MAX_DOCUMENTS=1,
        INGESTION_MAX_SOURCE_BYTES=1024 * 1024,
        _env_file=None,
    )

    with pytest.raises(ValueError, match="INGESTION_MAX_DOCUMENTS"):
        list(LocalCorpusLoader(settings).iter_load(source))


@pytest.mark.asyncio
async def test_bounded_admission_rejects_excess_work() -> None:
    """A busy model slot must reject excess requests instead of growing memory."""

    admission = BoundedAdmission(concurrency=1, queue_size=0, timeout_seconds=0.01)
    async with admission.acquire():
        with pytest.raises(AdmissionRejected):
            async with admission.acquire():
                pass


def test_sparse_search_uses_primary_database_and_preserves_filters(tmp_path: Path) -> None:
    """Sparse retrieval reads the same primary database rows as other services."""

    document = CanonicalDocument(
        document_id="doc-1",
        source_uri="file:///one.md",
        title="One",
        text="citation traceability",
        ingested_at=datetime.now(UTC),
        content_hash="hash",
    )
    chunk = ChunkPayload(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_uri=document.source_uri,
        title=document.title,
        text=document.text,
        token_count=2,
        char_start=0,
        char_end=len(document.text),
        embedding_model="test",
        embedding_version="v1",
        index_version="i1",
    )

    settings = Settings(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'citebot.db'}",
        OBJECT_STORAGE_PATH=tmp_path / "raw",
        STRUCTURED_DOCUMENT_PATH=tmp_path / "structured",
        SAMPLE_CORPUS_AUTO_INGEST=False,
        EMBEDDING_PROVIDER="local",
        ANSWER_PROVIDER="local",
        _env_file=None,
    )
    container = build_container(settings)

    async def exercise() -> list:
        await container.initialize()
        await container.ingestion_repository.save_document(document, [chunk], "raw.txt")
        results = await container.retrieval_service.search(
            SearchRequest(
                query="citation",
                strategy="sparse",
                filters=RetrievalFilters(source_uris=[document.source_uri]),
            )
        )
        await container.close()
        return results

    results = asyncio.run(exercise())
    assert results and results[0].chunk_id == "chunk-1"


def test_offline_profile_can_disable_local_dense_fallback() -> None:
    """The deployed PostgreSQL profile must not re-embed the corpus on outage."""

    settings = Settings(
        RUNTIME_MODE="development",
        ALLOW_LOCAL_DENSE_FALLBACK=False,
        _env_file=None,
    )
    container = build_container(settings)

    assert "local" not in container.retrieval_service._backend_order("auto")
