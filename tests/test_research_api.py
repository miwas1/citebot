"""Research agent API tests for orchestration, citations, and session memory."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_research_query_returns_cited_answer(
    configured_environment: Path,
) -> None:
    """The research API should return a grounded answer with stable citations."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    (corpus_path / "paper.md").write_text(
        (
            "# Citation Traceability\n\n"
            "Citation traceability depends on stable chunk identifiers, source "
            "locations, and grounded answer synthesis."
        ),
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )

        assert ingest_response.status_code == 200

        response = client.post(
            "/api/v1/research/query",
            json={
                "query": "How does citation traceability work in CiteBot?",
                "top_k": 3,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"]["direct_answer"]
        assert payload["answer"]["citations"]
        assert payload["answer"]["citations"][0]["chunk_id"]
        assert payload["trace_id"]
        assert payload["verification"]["overall_verdict"] in {
            "supported",
            "partially_supported",
        }


def test_research_session_preserves_context_after_follow_up(
    configured_environment: Path,
) -> None:
    """The research API should persist session context for follow-up questions."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    (corpus_path / "overview.md").write_text(
        (
            "Research sessions should preserve source-aware conversation memory. "
            "Compressed memory must retain citation identifiers for follow-up turns."
        ),
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )

        assert ingest_response.status_code == 200

        first_response = client.post(
            "/api/v1/research/query",
            json={
                "session_id": "session-1",
                "query": "What should a research session preserve?",
                "top_k": 3,
            },
        )
        second_response = client.post(
            "/api/v1/research/query",
            json={
                "session_id": "session-1",
                "query": "What about citations in follow-up turns?",
                "top_k": 3,
            },
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 200

        first_payload = first_response.json()
        second_payload = second_response.json()

        assert first_payload["session_id"] == "session-1"
        assert second_payload["session_id"] == "session-1"
        assert second_payload["answer"]["citations"]
        assert second_payload["memory"]["recent_turns"]
        assert second_payload["memory"]["citation_graph"]
