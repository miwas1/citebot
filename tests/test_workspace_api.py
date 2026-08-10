"""End-user workspace API tests for uploads, documents, and conversations."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_workspace_ui_is_served(configured_environment: Path) -> None:
    """The application root should serve the integrated user workspace."""

    with TestClient(create_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Ask your documents" in response.text


def test_browser_upload_is_ingested_and_listed(configured_environment: Path) -> None:
    """A streamed browser upload should become a ready library document."""

    with TestClient(create_app()) as client:
        upload = client.post(
            "/api/v1/documents/uploads?filename=research-notes.md",
            content=b"# Notes\n\nStable citations connect claims to local evidence.",
            headers={"Content-Type": "application/octet-stream"},
        )

        assert upload.status_code == 201
        assert upload.json()["job"]["status"] == "completed"

        library = client.get("/api/v1/documents")
        assert library.status_code == 200
        documents = library.json()
        assert len(documents) == 1
        assert documents[0]["title"] == "research-notes"
        assert documents[0]["chunk_count"] >= 1


def test_browser_upload_rejects_unsupported_extensions(
    configured_environment: Path,
) -> None:
    """The browser upload boundary should reject unapproved file types."""

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/documents/uploads?filename=payload.exe",
            content=b"not executable",
        )

    assert response.status_code == 415


def test_conversation_history_can_be_listed_and_deleted(
    configured_environment: Path,
) -> None:
    """Persisted research sessions should be manageable from the chat sidebar."""

    corpus = configured_environment / "corpus"
    corpus.mkdir()
    (corpus / "source.txt").write_text(
        "Private research workspaces retain cited conversation history.",
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        assert client.post(
            "/api/v1/admin/ingestion/jobs", json={"source_path": str(corpus)}
        ).status_code == 200
        assert client.post(
            "/api/v1/research/query",
            json={"session_id": "workspace-session", "query": "What is retained?"},
        ).status_code == 200

        history = client.get("/api/v1/conversations")
        assert history.status_code == 200
        assert history.json()[0]["session_id"] == "workspace-session"
        assert history.json()[0]["title"] == "What is retained?"

        assert client.delete("/api/v1/conversations/workspace-session").status_code == 204
        assert client.get("/api/v1/conversations/workspace-session").status_code == 404
