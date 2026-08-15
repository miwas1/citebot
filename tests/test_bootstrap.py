"""Tests for the first-run sample corpus bootstrap."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.models import DocumentRecord, IngestionJobRecord
from app.db.session import DatabaseSessionManager
from app.ingestion.bootstrap import ensure_sample_corpus
from app.ingestion.repository import IngestionRepository
from app.ingestion.schemas import JobStatusResponse


def test_bundled_sample_corpus_is_bounded_and_topic_consistent() -> None:
    """The demo project must contain exactly 100 machine-learning papers."""

    corpus_path = Path(__file__).parents[1] / "data" / "sample_corpus"
    corpus_files = list(corpus_path.iterdir())
    assert [path.name for path in corpus_files] == ["arxiv_papers.jsonl"]

    records = [
        json.loads(line)
        for line in corpus_files[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 100
    assert len({record["source_uri"] for record in records}) == 100
    assert {
        record["metadata"]["categories"][0] for record in records
    } == {"cs.LG"}


@pytest.mark.asyncio
async def test_sample_refresh_prunes_only_stale_bundled_documents(
    tmp_path: Path,
) -> None:
    """A corpus refresh removes old demo sources while preserving user uploads."""

    manager = DatabaseSessionManager(
        f"sqlite+aiosqlite:///{tmp_path / 'sample-refresh.db'}"
    )
    await manager.initialize()
    sample_path = tmp_path / "sample_corpus"
    sample_path.mkdir()
    allowed_uri = "http://arxiv.org/abs/allowed"
    user_uri = "file:///user-notes.md"
    async with manager.session() as session:
        for index, (source_uri, publisher) in enumerate(
            [
                (allowed_uri, "arXiv"),
                ("http://arxiv.org/abs/stale", "arXiv"),
                (str((sample_path / "overview.md").resolve()), None),
                ("local://roadmap-notes", "CiteBot Team"),
                (user_uri, None),
            ]
        ):
            session.add(
                DocumentRecord(
                    document_id=f"doc-{index}",
                    project_id="sample-project",
                    source_uri=source_uri,
                    title=f"Document {index}",
                    publisher=publisher,
                    content_hash=str(index) * 64,
                    raw_text_path=f"raw-{index}.txt",
                    metadata_json={},
                )
            )

    repository = IngestionRepository(manager)
    try:
        removed = await repository.prune_stale_sample_documents(
            "sample-project",
            {allowed_uri},
            sample_path,
        )
        remaining = await repository.list_documents(
            project_id="sample-project"
        )
    finally:
        await manager.close()

    assert removed == 3
    assert {document.source_uri for document in remaining} == {
        allowed_uri,
        user_uri,
    }


@pytest.mark.asyncio
async def test_old_sample_job_does_not_block_100_document_refresh(
    tmp_path: Path,
) -> None:
    """A completed 500-document bootstrap must not suppress the smaller corpus."""

    manager = DatabaseSessionManager(
        f"sqlite+aiosqlite:///{tmp_path / 'sample-job-version.db'}"
    )
    await manager.initialize()
    source_path = str((tmp_path / "sample_corpus").resolve())
    async with manager.session() as session:
        session.add(
            IngestionJobRecord(
                job_id="old-sample-job",
                project_id="sample-project",
                source_path=source_path,
                status="completed",
                force_reindex=False,
                embedding_version="test",
                index_version="v2",
                documents_seen=500,
            )
        )

    repository = IngestionRepository(manager)
    try:
        current = await repository.has_active_or_completed_job(
            source_path,
            "sample-project",
            expected_documents=100,
        )
    finally:
        await manager.close()

    assert current is False


@pytest.mark.asyncio
async def test_sample_corpus_bootstrap_is_idempotent(tmp_path: Path) -> None:
    """Only one durable bootstrap job is created for a fresh installation."""

    sample_path = tmp_path / "sample_corpus"
    sample_path.mkdir()
    settings = Settings(
        RUNTIME_MODE="offline",
        DATABASE_URL="postgresql+asyncpg://citebot:citebot@postgres:5432/citebot",
        SAMPLE_CORPUS_PATH=sample_path,
        SAMPLE_CORPUS_AUTO_INGEST=True,
        OBJECT_STORAGE_PATH=tmp_path / "storage" / "raw_documents",
        INGESTION_EXECUTION_MODE="queued",
        EVALUATION_EVALUATOR_PROVIDER="local",
    )

    class Repository:
        def __init__(self) -> None:
            self.scheduled = False

        async def has_active_or_completed_job(self, source_path: str) -> bool:
            return self.scheduled

    class Ingestion:
        def __init__(self, repository: Repository) -> None:
            self.repository = repository
            self.calls = 0
            self.project_ids = []

        async def enqueue_path(self, source_path, **kwargs):
            self.calls += 1
            self.project_ids.append(kwargs["project_id"])
            self.repository.scheduled = True
            return JobStatusResponse(
                job_id="sample-job",
                source_path=str(source_path),
                status="queued",
                force_reindex=False,
                embedding_version=kwargs["embedding_version"],
                index_version=kwargs["index_version"],
                started_at=datetime.now(UTC),
            )

    repository = Repository()
    ingestion = Ingestion(repository)
    first = await ensure_sample_corpus(settings, repository, ingestion)
    second = await ensure_sample_corpus(settings, repository, ingestion)

    assert first is not None
    assert second is None
    assert ingestion.calls == 1
    assert ingestion.project_ids == ["sample-project"]
