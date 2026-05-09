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


def test_dense_search_falls_back_to_local_backend(
    configured_environment: Path,
) -> None:
    """Dense search should fall back to the local backend when remote stores are disabled."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    (corpus_path / "retrieval.md").write_text(
        "Hybrid retrieval reranking improves retrieval precision and recall.",
        encoding="utf-8",
    )
    (corpus_path / "infra.md").write_text(
        "Kubernetes cluster autoscaling keeps workloads stable during spikes.",
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )

        assert ingest_response.status_code == 200

        search_response = client.post(
            "/api/v1/admin/ingestion/search",
            json={
                "query": "hybrid retrieval reranking",
                "top_k": 2,
                "strategy": "dense",
                "include_explain": True,
            },
        )

        assert search_response.status_code == 200
        results = search_response.json()
        assert results
        assert "hybrid retrieval reranking" in results[0]["text"].lower()
        assert results[0]["source_backend"] == "local"
        assert results[0]["dense_score"] is not None
        assert results[0]["explain"]["used_backend"] == "local"


def test_hybrid_search_returns_fused_and_reranked_scores(
    configured_environment: Path,
) -> None:
    """Hybrid retrieval should expose dense, sparse, fused, and reranked scores."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    (corpus_path / "citations.md").write_text(
        (
            "Citation traceability depends on stable chunk identifiers. "
            "Citation traceability also requires reranking the strongest support."
        ),
        encoding="utf-8",
    )
    (corpus_path / "notes.md").write_text(
        "Traceable answers need careful indexing and retrieval evaluation.",
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )

        assert ingest_response.status_code == 200

        search_response = client.post(
            "/api/v1/admin/ingestion/search",
            json={
                "query": "citation traceability",
                "top_k": 2,
                "strategy": "hybrid",
                "include_explain": True,
            },
        )

        assert search_response.status_code == 200
        results = search_response.json()
        assert results
        assert results[0]["source_backend"] == "hybrid"
        assert results[0]["dense_score"] is not None
        assert results[0]["sparse_score"] is not None
        assert results[0]["fused_score"] is not None
        assert results[0]["rerank_score"] is not None
        assert results[0]["explain"]["fusion_method"] == "weighted_rrf"


def test_search_filters_limit_results_by_source_uri(
    configured_environment: Path,
) -> None:
    """Retrieval filters should constrain results before ranking is returned."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    alpha_path = corpus_path / "alpha.md"
    beta_path = corpus_path / "beta.md"
    alpha_path.write_text(
        "Dense retrieval recall improves with better query embeddings.",
        encoding="utf-8",
    )
    beta_path.write_text(
        "Dense retrieval recall also benefits from hybrid fusion.",
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )

        assert ingest_response.status_code == 200

        search_response = client.post(
            "/api/v1/admin/ingestion/search",
            json={
                "query": "dense retrieval recall",
                "top_k": 5,
                "strategy": "hybrid",
                "filters": {"source_uris": [str(alpha_path)]},
            },
        )

        assert search_response.status_code == 200
        results = search_response.json()
        assert results
        assert all(result["source_uri"] == str(alpha_path) for result in results)
