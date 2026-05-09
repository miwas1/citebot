"""API contract tests for auth, rate limiting, streaming, and observability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def _write_research_corpus(corpus_path: Path) -> None:
    """Write a small grounded corpus fixture for research API contract tests."""

    corpus_path.mkdir()
    (corpus_path / "paper.md").write_text(
        (
            "# Citation Traceability\n\n"
            "Citation traceability depends on stable chunk identifiers, source "
            "locations, and grounded answer synthesis."
        ),
        encoding="utf-8",
    )


def _parse_stream_lines(response: TestClient) -> list[dict[str, object]]:
    """Parse newline-delimited JSON events from a streaming response."""

    body = response.read().decode("utf-8")
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def test_research_session_persists_across_app_restarts(
    configured_environment: Path,
) -> None:
    """Research sessions should survive a process restart through durable storage."""

    corpus_path = configured_environment / "corpus"
    _write_research_corpus(corpus_path)

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )
        assert ingest_response.status_code == 200

        first_response = client.post(
            "/api/v1/research/query",
            json={
                "session_id": "restart-safe-session",
                "query": "What does citation traceability depend on?",
                "top_k": 3,
            },
        )
        assert first_response.status_code == 200

    with TestClient(create_app()) as client:
        second_response = client.post(
            "/api/v1/research/query",
            json={
                "session_id": "restart-safe-session",
                "query": "What about source locations in follow-up turns?",
                "top_k": 3,
            },
        )

    assert second_response.status_code == 200
    payload = second_response.json()
    assert payload["session_id"] == "restart-safe-session"
    assert len(payload["memory"]["recent_turns"]) == 4
    assert payload["memory"]["citation_graph"]


def test_research_query_requires_api_key_when_configured(
    configured_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research routes should enforce the configured research API key."""

    monkeypatch.setenv("RESEARCH_API_KEY", "research-secret")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        unauthorized = client.post(
            "/api/v1/research/query",
            json={"query": "Is auth enabled?"},
        )
        authorized = client.post(
            "/api/v1/research/query",
            json={"query": "Is auth enabled?"},
            headers={"X-API-Key": "research-secret"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_research_query_rate_limit_is_enforced(
    configured_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research requests should return 429 after exceeding the configured limit."""

    monkeypatch.setenv("RESEARCH_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        first_response = client.post(
            "/api/v1/research/query",
            json={"query": "First request"},
        )
        second_response = client.post(
            "/api/v1/research/query",
            json={"query": "Second request"},
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 429


def test_streaming_research_query_emits_start_and_complete_events(
    configured_environment: Path,
) -> None:
    """The streaming research endpoint should emit an initial and final event."""

    corpus_path = configured_environment / "corpus"
    _write_research_corpus(corpus_path)

    with TestClient(create_app()) as client:
        ingest_response = client.post(
            "/api/v1/admin/ingestion/jobs",
            json={"source_path": str(corpus_path)},
        )
        assert ingest_response.status_code == 200

        with client.stream(
            "POST",
            "/api/v1/research/query/stream",
            json={
                "query": "How does citation traceability work?",
                "top_k": 3,
            },
        ) as response:
            payload = _parse_stream_lines(response)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert payload[0]["event"] == "start"
    assert payload[1]["event"] == "complete"
    assert payload[1]["data"]["answer"]["citations"]


def test_metrics_endpoint_requires_admin_key_and_reports_requests(
    configured_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The metrics endpoint should require admin auth and expose request metrics."""

    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        health_response = client.get("/api/v1/health")
        assert health_response.status_code == 200

        unauthorized = client.get("/api/v1/metrics")
        authorized = client.get(
            "/api/v1/metrics",
            headers={"X-API-Key": "admin-secret"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    payload = authorized.json()
    assert payload["requests"]
    assert any(item["path"] == "/api/v1/health" for item in payload["requests"])
