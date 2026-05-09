"""Evaluation API tests for the admin evaluation workflow."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def write_eval_fixture(dataset_path: Path, source_uri: str) -> None:
    """Write a minimal evaluation dataset fixture for API integration tests."""

    dataset_path.write_text(
        (
            "{\n"
            '  "dataset_name": "api_smoke",\n'
            '  "dataset_version": "1",\n'
            '  "cases": [\n'
            "    {\n"
            '      "eval_case_id": "roadmap-case",\n'
            '      "question": "When should re-indexing be triggered?",\n'
            '      "expected_answer_traits": ["embedding versions change", "quality thresholds regress"],\n'
            '      "reference_answer": "Re-indexing should be triggered when embedding versions change or quality thresholds regress.",\n'
            f'      "expected_source_uris": ["{source_uri}"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )


def test_evaluation_api_runs_and_fetches_saved_artifact(
    configured_environment: Path,
) -> None:
    """The admin evaluation API should execute a run and return its stored artifact."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    roadmap_path = corpus_path / "roadmap.json"
    roadmap_path.write_text(
        (
            "{\n"
            '  "title": "Roadmap Notes",\n'
            '  "source_uri": "local://roadmap-notes",\n'
            '  "text": "Hybrid retrieval combines dense semantic search with sparse keyword matching. '
            'Re-indexing should be triggered when embedding versions change or quality thresholds regress."\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    dataset_path = configured_environment / "evaluation.json"
    write_eval_fixture(dataset_path, "local://roadmap-notes")

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/admin/evaluation/runs",
            json={
                "dataset_path": str(dataset_path),
                "source_path": str(corpus_path),
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["dataset_name"] == "api_smoke"
        assert payload["case_results"]
        assert payload["case_results"][0]["passed"] is True

        lookup_response = client.get(
            f"/api/v1/admin/evaluation/runs/{payload['run_id']}"
        )

        assert lookup_response.status_code == 200
        lookup_payload = lookup_response.json()
        assert lookup_payload["run_id"] == payload["run_id"]
