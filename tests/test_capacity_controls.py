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
from app.ingestion.schemas import CanonicalDocument, ChunkPayload, RetrievalFilters
from app.ingestion.sparse_index import SparseIndex


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


def test_sparse_index_uses_fts_and_preserves_filters(tmp_path: Path) -> None:
    """The sparse index is transactional SQLite FTS5, not a whole-file JSON scan."""

    index = SparseIndex(tmp_path / "sparse_index.json")
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

    async def exercise() -> list:
        await index.initialize()
        await index.replace_document_chunks(document, [chunk])
        return await index.search(
            "citation",
            filters=RetrievalFilters(source_uris=[document.source_uri]),
        )

    results = asyncio.run(exercise())
    assert results and results[0].chunk_id == "chunk-1"
    assert (tmp_path / "sparse_index.sqlite3").is_file()


def test_offline_profile_can_disable_local_dense_fallback() -> None:
    """The deployed Qdrant profile must not re-embed the corpus on outage."""

    settings = Settings(
        RUNTIME_MODE="development",
        ENABLE_QDRANT=True,
        ALLOW_LOCAL_DENSE_FALLBACK=False,
        _env_file=None,
    )
    container = build_container(settings)

    assert "local" not in container.retrieval_service._backend_order("auto")
