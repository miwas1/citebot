"""Project lifecycle, scoped ingestion, and retrieval isolation contracts."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _create_project(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["project_id"]


def _upload(client: TestClient, project_id: str, content: bytes) -> None:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents/uploads?filename=source.txt",
        content=content,
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 201
    assert response.json()["job"]["project_id"] == project_id


def test_sample_project_is_created_and_project_uploads_are_scoped(
    configured_environment: Path,
) -> None:
    """A clean database exposes deterministic system projects and scoped documents."""

    with TestClient(create_app()) as client:
        projects = client.get("/api/v1/projects")
        assert projects.status_code == 200
        assert {row["project_id"] for row in projects.json()} == {"sample-project"}

        project_id = _create_project(client, "Alpha Team")
        _upload(client, project_id, b"Alpha-only evidence for this team.")

        documents = client.get(f"/api/v1/projects/{project_id}/documents")
        assert documents.status_code == 200
        assert len(documents.json()) == 1
        assert documents.json()[0]["project_id"] == project_id
        assert client.get("/api/v1/documents").json() == []


def test_identical_sources_get_independent_ids_and_search_scope(
    configured_environment: Path,
) -> None:
    """Two projects may ingest the same filename without cross-project matches."""

    with TestClient(create_app()) as client:
        first = _create_project(client, "First Team")
        second = _create_project(client, "Second Team")
        _upload(client, first, b"First team confidential marker.")
        _upload(client, second, b"Second team confidential marker.")

        first_document = client.get(f"/api/v1/projects/{first}/documents").json()[0]
        second_document = client.get(f"/api/v1/projects/{second}/documents").json()[0]
        assert first_document["document_id"] != second_document["document_id"]

        first_results = client.post(
            "/api/v1/admin/ingestion/search",
            json={"query": "confidential marker", "filters": {"project_id": first}},
        )
        second_results = client.post(
            "/api/v1/admin/ingestion/search",
            json={"query": "confidential marker", "filters": {"project_id": second}},
        )
        assert first_results.status_code == 200
        assert second_results.status_code == 200
        assert {row["project_id"] for row in first_results.json()} == {first}
        assert {row["project_id"] for row in second_results.json()} == {second}


def test_conversations_cannot_cross_project_boundaries(configured_environment: Path) -> None:
    """A session ID created in one project is not reusable from another."""

    with TestClient(create_app()) as client:
        first = _create_project(client, "Conversation A")
        second = _create_project(client, "Conversation B")
        _upload(client, first, b"Conversation A source.")

        query = client.post(
            f"/api/v1/projects/{first}/research/query",
            json={"session_id": "project-session", "query": "What is here?"},
        )
        assert query.status_code == 200

        cross_project = client.post(
            f"/api/v1/projects/{second}/research/query",
            json={"session_id": "project-session", "query": "What is here?"},
        )
        assert cross_project.status_code == 404
