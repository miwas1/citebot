"""Integration tests for the Phase 2 ingestion flow."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_ingestion_job_indexes_documents_and_searches_chunks(
    configured_environment: Path,
) -> None:
    """The admin ingestion endpoint should index a corpus and expose searchable chunks."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    (corpus_path / "paper.md").write_text(
        (
            "# Retrieval\n\nCitation traceability depends on stable chunk identifiers "
            "and source offsets."
        ),
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )

        assert ingest_response.status_code == 200
        job = ingest_response.json()
        assert job["status"] == "completed"
        assert job["documents_indexed"] == 1
        assert job["chunks_written"] >= 1

        search_response = client.post(
            "/api/v1/admin/ingestion/search",
            json={"query": "citation traceability", "top_k": 3},
        )

        assert search_response.status_code == 200
        results = search_response.json()
        assert results
        assert results[0]["document_id"]
        assert "citation traceability" in results[0]["text"].lower()


def test_reingesting_unchanged_documents_is_idempotent(
    configured_environment: Path,
) -> None:
    """A second ingestion run should skip unchanged documents instead of rewriting them."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    (corpus_path / "notes.txt").write_text(
        "Hybrid retrieval blends dense and sparse signals for better recall.",
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        first_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )
        second_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        second_job = second_response.json()
        assert second_job["documents_skipped"] == 1
        assert second_job["documents_indexed"] == 0
