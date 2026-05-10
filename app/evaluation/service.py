"""Evaluation runner for quality monitoring over the real research pipeline."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.schemas import ResearchQueryRequest
from app.agents.service import ResearchAgentService
from app.core.config import Settings
from app.evaluation.evaluator import build_evaluator_binding, use_evaluator_environment
from app.evaluation.metrics import aggregate_metrics, compute_case_metrics
from app.evaluation.schemas import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRunRequest,
    EvaluationRunResult,
    RagasEvaluationSummary,
    TraceExportSummary,
    artifact_file_name,
)
from app.ingestion.service import IngestionService


class EvaluationService:
    """Execute versioned evaluation datasets against the research agent."""

    def __init__(
        self,
        settings: Settings,
        ingestion_service: IngestionService,
        research_agent_service: ResearchAgentService,
    ) -> None:
        """Store dependencies and artifact configuration for eval runs."""

        self._settings = settings
        self._ingestion_service = ingestion_service
        self._research_agent_service = research_agent_service

    async def run(self, request: EvaluationRunRequest) -> EvaluationRunResult:
        """Run one evaluation dataset and persist its result artifact."""

        dataset_path = request.dataset_path or self._settings.evaluation_dataset_path
        dataset = self._load_dataset(dataset_path)
        if request.source_path is not None:
            await self._ingestion_service.ingest_path(
                source_path=request.source_path,
                force_reindex=request.force_reindex,
                embedding_version=request.embedding_version,
                index_version=request.index_version,
            )
        run_result = EvaluationRunResult(
            dataset_name=dataset.dataset_name,
            dataset_version=dataset.dataset_version,
            threshold_mode=request.threshold_mode,
            run_ragas=request.run_ragas,
            metadata={
                "dataset_path": str(dataset_path),
                "source_path": (
                    str(request.source_path) if request.source_path else None
                ),
                "embedding_version": request.embedding_version,
                "index_version": request.index_version,
                "app_version": self._settings.app_version,
                "embedding_provider": self._settings.embedding_provider,
                "answer_provider": self._settings.answer_provider,
            },
        )
        run_result.started_at = datetime.now(UTC)
        run_result.case_results = [
            await self._execute_case(run_result.run_id, case) for case in dataset.cases
        ]
        run_result.summary_metrics = aggregate_metrics(run_result.case_results)
        run_result.ragas = await self._run_ragas(dataset.cases, run_result, request)
        run_result.threshold_failures = self._build_run_threshold_failures(
            run_result.case_results,
            run_result.summary_metrics,
            run_result.ragas,
            request,
        )
        run_result.status = "failed" if run_result.threshold_failures else "completed"
        run_result.finished_at = datetime.now(UTC)
        trace_export = self._write_trace_export(run_result)
        run_result.trace_export = trace_export
        run_result.artifact_path = str(self._persist_run(run_result))
        return run_result

    async def get_run(self, run_id: str) -> EvaluationRunResult | None:
        """Load one persisted evaluation run artifact if it exists."""

        artifact_path = self._artifact_path(run_id)
        if not artifact_path.exists():
            return None
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        return EvaluationRunResult.model_validate(payload)

    def _load_dataset(self, dataset_path: Path) -> EvaluationDataset:
        """Load and validate a versioned evaluation dataset from disk."""

        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        return EvaluationDataset.model_validate(payload)

    async def _execute_case(
        self,
        run_id: str,
        case: EvaluationCase,
    ) -> EvaluationCaseResult:
        """Execute one evaluation case through the research agent."""

        session_id = f"eval-{run_id}-{case.eval_case_id}"
        await self._prime_conversation_history(session_id, case)
        response = await self._research_agent_service.answer(
            ResearchQueryRequest(
                session_id=session_id,
                query=case.question,
                top_k=case.top_k,
                allow_web_search=case.allow_web_search,
                allow_python_execution=case.allow_python_execution,
                freshness_required=case.freshness_required,
                include_debug_trace=True,
            )
        )
        metrics = compute_case_metrics(case, response)
        threshold_failures = self._build_case_threshold_failures(metrics)
        return EvaluationCaseResult(
            eval_case_id=case.eval_case_id,
            session_id=session_id,
            trace_id=response.trace_id,
            question=case.question,
            direct_answer=response.answer.direct_answer,
            retrieved_chunk_ids=[
                context.chunk_id for context in response.retrieved_contexts
            ],
            retrieved_document_ids=[
                context.document_id for context in response.retrieved_contexts
            ],
            retrieved_source_uris=[
                context.source_uri for context in response.retrieved_contexts
            ],
            citation_chunk_ids=[
                citation.chunk_id for citation in response.answer.citations
            ],
            metrics=metrics,
            threshold_failures=threshold_failures,
            passed=not threshold_failures,
            error=response.error,
        )

    async def _prime_conversation_history(
        self,
        session_id: str,
        case: EvaluationCase,
    ) -> None:
        """Replay prior user turns so multi-turn eval cases use the real session store."""

        for turn in case.conversation_history:
            if turn.role != "user":
                continue
            await self._research_agent_service.answer(
                ResearchQueryRequest(
                    session_id=session_id,
                    query=turn.content,
                    top_k=case.top_k,
                    allow_web_search=case.allow_web_search,
                    allow_python_execution=case.allow_python_execution,
                    freshness_required=False,
                    include_debug_trace=False,
                )
            )

    async def _run_ragas(
        self,
        cases: list[EvaluationCase],
        run_result: EvaluationRunResult,
        request: EvaluationRunRequest,
    ) -> RagasEvaluationSummary:
        """Run optional RAGAS metrics without making them mandatory for local smoke runs."""

        if not (request.run_ragas or self._settings.evaluation_enable_ragas):
            return RagasEvaluationSummary(status="disabled")
        try:
            evaluator_binding = build_evaluator_binding(self._settings)
        except ValueError as error:
            return RagasEvaluationSummary(
                status="failed",
                message=str(error),
                evaluator_provider=self._settings.evaluation_evaluator_provider,
                evaluator_model=self._settings.evaluation_evaluator_model,
            )
        ragas_rows = [
            {
                "question": case.question,
                "answer": case_result.direct_answer,
                "contexts": case_result.retrieved_source_uris,
                "ground_truth": case.reference_answer,
            }
            for case, case_result in zip(cases, run_result.case_results, strict=True)
            if case.reference_answer
        ]
        if not ragas_rows:
            return RagasEvaluationSummary(
                status="skipped",
                message="No evaluation cases provided reference_answer values for RAGAS.",
                evaluator_provider=evaluator_binding.provider,
                evaluator_model=evaluator_binding.model,
            )
        return await asyncio.to_thread(
            self._evaluate_ragas_rows,
            ragas_rows,
            request,
            evaluator_binding,
        )

    def _evaluate_ragas_rows(
        self,
        ragas_rows: list[dict[str, Any]],
        request: EvaluationRunRequest,
        evaluator_binding,
    ) -> RagasEvaluationSummary:
        """Run the optional RAGAS dependency inside a worker thread."""

        with use_evaluator_environment(evaluator_binding):
            try:
                from datasets import Dataset
                from ragas import evaluate
                from ragas.metrics import (
                    answer_relevancy,
                    context_precision,
                    context_recall,
                    faithfulness,
                )
            except ImportError:
                return RagasEvaluationSummary(
                    status="skipped",
                    message=(
                        "RAGAS dependencies are not installed. Install citebot[evaluation] "
                        "to enable run-level RAGAS scoring."
                    ),
                    evaluator_provider=evaluator_binding.provider,
                    evaluator_model=evaluator_binding.model,
                )
            dataset = Dataset.from_dict(
                {
                    "question": [row["question"] for row in ragas_rows],
                    "answer": [row["answer"] for row in ragas_rows],
                    "contexts": [row["contexts"] for row in ragas_rows],
                    "ground_truth": [row["ground_truth"] for row in ragas_rows],
                }
            )
            try:
                result = evaluate(
                    dataset,
                    metrics=[
                        answer_relevancy,
                        faithfulness,
                        context_recall,
                        context_precision,
                    ],
                    in_ci=request.threshold_mode == "ci",
                )
            except (
                Exception
            ) as error:  # pragma: no cover - dependent on optional extras
                return RagasEvaluationSummary(
                    status="failed",
                    message=str(error),
                    evaluator_provider=evaluator_binding.provider,
                    evaluator_model=evaluator_binding.model,
                )
        scores = {
            key: float(value)
            for key, value in dict(result).items()
            if isinstance(value, int | float)
        }
        return RagasEvaluationSummary(
            status="completed",
            scores=scores,
            evaluator_provider=evaluator_binding.provider,
            evaluator_model=evaluator_binding.model,
        )

    def _build_case_threshold_failures(self, metrics: dict[str, float]) -> list[str]:
        """Return threshold failures for a single evaluation case."""

        failures: list[str] = []
        if (
            "context_precision" in metrics
            and metrics["context_precision"]
            < self._settings.evaluation_context_precision_threshold
        ):
            failures.append("context_precision")
        if (
            metrics.get("citation_retrieval_precision", 1.0)
            < self._settings.evaluation_citation_support_threshold
        ):
            failures.append("citation_retrieval_precision")
        if (
            metrics.get("verification_pass_rate", 1.0)
            < self._settings.evaluation_faithfulness_threshold
        ):
            failures.append("verification_pass_rate")
        if (
            "reference_overlap_f1" in metrics
            and metrics["reference_overlap_f1"]
            < self._settings.evaluation_answer_relevance_threshold
        ):
            failures.append("reference_overlap_f1")
        if (
            "trait_coverage" in metrics
            and metrics["trait_coverage"]
            < self._settings.evaluation_answer_relevance_threshold
        ):
            failures.append("trait_coverage")
        return failures

    def _build_run_threshold_failures(
        self,
        case_results: list[EvaluationCaseResult],
        summary_metrics: dict[str, float],
        ragas: RagasEvaluationSummary,
        request: EvaluationRunRequest,
    ) -> list[str]:
        """Return run-level threshold failures for report or CI mode."""

        failures = [
            f"case:{case_result.eval_case_id}:{failure}"
            for case_result in case_results
            for failure in case_result.threshold_failures
            if request.threshold_mode == "ci"
        ]
        if request.threshold_mode != "ci":
            return failures
        if (
            summary_metrics.get("citation_retrieval_precision", 1.0)
            < self._settings.evaluation_citation_support_threshold
        ):
            failures.append("summary:citation_retrieval_precision")
        if (
            summary_metrics.get("verification_pass_rate", 1.0)
            < self._settings.evaluation_faithfulness_threshold
        ):
            failures.append("summary:verification_pass_rate")
        if (
            "context_precision" in summary_metrics
            and summary_metrics["context_precision"]
            < self._settings.evaluation_context_precision_threshold
        ):
            failures.append("summary:context_precision")
        if ragas.status == "completed":
            if (
                ragas.scores.get("faithfulness", 1.0)
                < self._settings.evaluation_faithfulness_threshold
            ):
                failures.append("ragas:faithfulness")
            if (
                ragas.scores.get("context_precision", 1.0)
                < self._settings.evaluation_context_precision_threshold
            ):
                failures.append("ragas:context_precision")
            if (
                ragas.scores.get("answer_relevancy", 1.0)
                < self._settings.evaluation_answer_relevance_threshold
            ):
                failures.append("ragas:answer_relevancy")
        elif request.run_ragas and self._settings.evaluation_ci_fail_on_missing_ragas:
            failures.append("ragas:missing")
        return failures

    def _persist_run(self, run_result: EvaluationRunResult) -> Path:
        """Persist the evaluation run artifact to the configured artifact directory."""

        artifact_path = self._artifact_path(run_result.run_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(run_result.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return artifact_path

    def _write_trace_export(
        self, run_result: EvaluationRunResult
    ) -> TraceExportSummary:
        """Write a trace manifest that can be shipped to Phoenix later."""

        if not self._settings.evaluation_phoenix_endpoint:
            return TraceExportSummary(status="disabled")
        artifact_dir = self._settings.evaluation_artifact_dir / "trace_manifests"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{run_result.run_id}.json"
        payload = {
            "run_id": run_result.run_id,
            "endpoint": self._settings.evaluation_phoenix_endpoint,
            "sample_rate": self._settings.evaluation_phoenix_sample_rate,
            "traces": [
                {
                    "eval_case_id": case_result.eval_case_id,
                    "session_id": case_result.session_id,
                    "trace_id": case_result.trace_id,
                }
                for case_result in run_result.case_results
            ],
        }
        artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return TraceExportSummary(
            status="recorded",
            endpoint=self._settings.evaluation_phoenix_endpoint,
            trace_count=len(run_result.case_results),
            artifact_path=str(artifact_path),
        )

    def _artifact_path(self, run_id: str) -> Path:
        """Return the artifact path for a stored evaluation run."""

        return self._settings.evaluation_artifact_dir / artifact_file_name(run_id)
