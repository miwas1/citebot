"""API smoke tests."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_returns_ok() -> None:
    """The liveness probe should return a successful status payload."""

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_version_endpoint_returns_application_metadata() -> None:
    """The version endpoint should expose the app name and version."""

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/version")

        assert response.status_code == 200
        assert response.json()["name"] == "CiteBot"
