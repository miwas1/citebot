"""Evaluation service tests for Phase 8 local quality monitoring."""

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.lifecycle import build_container
from app.evaluation.schemas import EvaluationRunRequest


def write_eval_fixture(dataset_path: Path, source_uri: str) -> None:
    """Write a minimal evaluation dataset fixture for the current test corpus."""

    dataset_path.write_text(
        (
            "{\n"
            '  "dataset_name": "test_smoke",\n'
            '  "dataset_version": "1",\n'
            '  "cases": [\n'
            "    {\n"
            '      "eval_case_id": "citation-case",\n'
            '      "question": "How does citation traceability work?",\n'
            '      "expected_answer_traits": ["stable chunk identifiers", "character offsets"],\n'
            '      "reference_answer": "Citation traceability depends on stable chunk identifiers and character offsets.",\n'
            f'      "expected_source_uris": ["{source_uri}"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_evaluation_service_runs_and_persists_artifact(
    configured_environment: Path,
) -> None:
    """The evaluation service should run a dataset and persist a loadable artifact."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    document_path = corpus_path / "paper.md"
    document_path.write_text(
        (
            "# Citation Traceability\n\n"
            "Citation traceability depends on stable chunk identifiers, source "
            "locations, and character offsets."
        ),
        encoding="utf-8",
    )
    dataset_path = configured_environment / "evaluation.json"
    write_eval_fixture(dataset_path, document_path.as_uri())

    container = build_container(get_settings())
    await container.initialize()
    try:
        result = await container.evaluation_service.run(
            EvaluationRunRequest(
                dataset_path=dataset_path,
                source_path=corpus_path,
            )
        )
        reloaded = await container.evaluation_service.get_run(result.run_id)
    finally:
        await container.close()

    assert result.status == "completed"
    assert result.case_results
    assert result.case_results[0].metrics["citation_retrieval_precision"] == 1.0
    assert result.artifact_path is not None
    assert reloaded is not None
    assert reloaded.run_id == result.run_id


@pytest.mark.asyncio
@pytest.mark.ragas_ci
async def test_evaluation_service_applies_ci_thresholds(
    configured_environment: Path,
) -> None:
    """The CI-mode evaluation gate should pass for a grounded local smoke case."""

    corpus_path = configured_environment / "corpus"
    corpus_path.mkdir()
    document_path = corpus_path / "paper.md"
    document_path.write_text(
        (
            "# Citation Traceability\n\n"
            "Citation traceability depends on stable chunk identifiers, source "
            "locations, and character offsets."
        ),
        encoding="utf-8",
    )
    dataset_path = configured_environment / "evaluation.json"
    write_eval_fixture(dataset_path, document_path.as_uri())

    container = build_container(get_settings())
    await container.initialize()
    try:
        result = await container.evaluation_service.run(
            EvaluationRunRequest(
                dataset_path=dataset_path,
                source_path=corpus_path,
                threshold_mode="ci",
            )
        )
    finally:
        await container.close()

    assert result.status == "completed"
    assert result.threshold_failures == []
