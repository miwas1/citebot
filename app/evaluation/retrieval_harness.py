"""Docker-backed retrieval integration and benchmark harness."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


@dataclass(slots=True)
class BenchmarkQuery:
    """One retrieval query used by the benchmark and integration harness."""

    name: str
    query: str
    top_k: int = 3
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class QueryExecution:
    """Captured result metadata for one backend query execution."""

    backend: str
    query_name: str
    latency_ms: float
    result_count: int
    top_chunk_ids: list[str]
    top_score: float | None
    source_backend: str | None


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for benchmark and integration runs."""

    parser = argparse.ArgumentParser(
        description="Run Docker-backed retrieval integration checks and benchmarks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("integration", "benchmark"):
        command_parser = subparsers.add_parser(command_name)
        command_parser.add_argument(
            "--api-base-url",
            default="http://127.0.0.1:8000/api/v1",
        )
        command_parser.add_argument(
            "--ingest-source-path",
            default="/app/data/sample_corpus",
        )
        command_parser.add_argument(
            "--queries-file",
            type=Path,
            default=_workspace_root() / "data" / "retrieval_benchmark_queries.json",
        )
        command_parser.add_argument("--timeout-seconds", type=float, default=60.0)
        command_parser.add_argument("--start-compose", action="store_true")
        command_parser.add_argument("--stop-compose", action="store_true")
        command_parser.add_argument(
            "--compose-services",
            nargs="*",
            default=["postgres", "qdrant", "redis", "api"],
        )
        command_parser.add_argument(
            "--artifact-dir",
            type=Path,
            default=_workspace_root() / "artifacts" / "retrieval-benchmarks",
        )
    benchmark_parser = subparsers.choices["benchmark"]
    benchmark_parser.add_argument("--iterations", type=int, default=5)
    benchmark_parser.add_argument("--warmup-iterations", type=int, default=1)
    benchmark_parser.add_argument(
        "--strategy",
        choices=["dense", "hybrid"],
        default="dense",
    )
    benchmark_parser.add_argument("--enable-reranking", action="store_true")
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Execute the requested harness command and return its exit status."""

    parser = build_parser()
    args = parser.parse_args(argv)
    stack_started = False
    if args.start_compose:
        start_compose_stack(_workspace_root(), args.compose_services)
        stack_started = True
    try:
        with httpx.Client(
            base_url=args.api_base_url, timeout=args.timeout_seconds
        ) as client:
            readiness_payload = wait_for_ready(
                client, timeout_seconds=args.timeout_seconds
            )
            ingestion_payload = ingest_sample_corpus(
                client,
                ingest_source_path=args.ingest_source_path,
            )
            queries = load_queries(args.queries_file)
            if args.command == "integration":
                report = run_integration_suite(client, queries)
            else:
                report = run_benchmark_suite(
                    client,
                    queries,
                    iterations=args.iterations,
                    warmup_iterations=args.warmup_iterations,
                    strategy=args.strategy,
                    enable_reranking=args.enable_reranking,
                )
            report["readiness"] = readiness_payload
            report["ingestion"] = ingestion_payload
            artifact_path = write_report(
                artifact_dir=args.artifact_dir,
                command_name=args.command,
                report=report,
            )
            print(render_report_summary(report, artifact_path))
    finally:
        if stack_started and args.stop_compose:
            stop_compose_stack(_workspace_root())
    return 0


def main() -> None:
    """Run the retrieval harness as a console entry point."""

    raise SystemExit(run_cli())


def start_compose_stack(workspace_root: Path, services: Sequence[str]) -> None:
    """Start the requested Docker Compose services in detached mode."""

    run_subprocess(
        ["docker", "compose", "up", "-d", "--build", *services],
        cwd=workspace_root,
    )


def stop_compose_stack(workspace_root: Path) -> None:
    """Stop the Docker Compose stack without removing named volumes."""

    run_subprocess(["docker", "compose", "down"], cwd=workspace_root)


def run_subprocess(command: Sequence[str], cwd: Path) -> None:
    """Run a subprocess command and raise a readable error on failure."""

    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"Required command not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        joined_command = " ".join(command)
        raise RuntimeError(f"Command failed: {joined_command}") from error


def wait_for_ready(client: httpx.Client, timeout_seconds: float) -> dict[str, Any]:
    """Wait until the API readiness endpoint returns a ready payload."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = client.get("/ready")
            if response.status_code == 200:
                payload = response.json()
                if payload.get("status") == "ready":
                    return payload
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    msg = f"Timed out waiting for readiness at {client.base_url}"
    raise RuntimeError(msg)


def ingest_sample_corpus(
    client: httpx.Client,
    ingest_source_path: str,
) -> dict[str, Any]:
    """Trigger sample corpus ingestion through the admin ingestion API."""

    response = client.post(
        "/admin/ingestion/jobs",
        json={"source_path": ingest_source_path},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "completed":
        msg = "Sample corpus ingestion did not complete successfully"
        raise RuntimeError(msg)
    return payload


def load_queries(queries_file: Path) -> list[BenchmarkQuery]:
    """Load benchmark queries from a JSON fixture file."""

    payload = json.loads(queries_file.read_text(encoding="utf-8"))
    return [BenchmarkQuery(**item) for item in payload]


def run_integration_suite(
    client: httpx.Client,
    queries: Sequence[BenchmarkQuery],
) -> dict[str, Any]:
    """Run backend integration checks and compare result overlap."""

    per_backend: dict[str, list[QueryExecution]] = {}
    for backend in ("pgvector", "qdrant"):
        executions: list[QueryExecution] = []
        for query in queries:
            executions.append(
                execute_query(
                    client,
                    backend=backend,
                    query=query,
                    strategy="dense",
                    include_explain=True,
                    enable_reranking=False,
                )
            )
        per_backend[backend] = executions
    comparisons = [
        compare_query_results(
            per_backend["pgvector"][index], per_backend["qdrant"][index]
        )
        for index in range(len(queries))
    ]
    failures = [comparison for comparison in comparisons if not comparison["passed"]]
    report = {
        "command": "integration",
        "executed_at": _timestamp(),
        "queries": [asdict(query) for query in queries],
        "backends": {
            backend: [asdict(execution) for execution in executions]
            for backend, executions in per_backend.items()
        },
        "comparisons": comparisons,
        "status": "passed" if not failures else "failed",
        "failure_count": len(failures),
    }
    if failures:
        msg = f"Integration checks failed for {len(failures)} query comparisons"
        raise RuntimeError(msg + "\n" + json.dumps(report, indent=2))
    return report


def run_benchmark_suite(
    client: httpx.Client,
    queries: Sequence[BenchmarkQuery],
    iterations: int,
    warmup_iterations: int,
    strategy: str,
    enable_reranking: bool,
) -> dict[str, Any]:
    """Benchmark pgvector and Qdrant retrieval latency through the API."""

    benchmark_results: dict[str, dict[str, Any]] = {}
    for backend in ("pgvector", "qdrant"):
        for _ in range(warmup_iterations):
            for query in queries:
                execute_query(
                    client,
                    backend=backend,
                    query=query,
                    strategy=strategy,
                    include_explain=False,
                    enable_reranking=enable_reranking,
                )
        executions: list[QueryExecution] = []
        for _ in range(iterations):
            for query in queries:
                executions.append(
                    execute_query(
                        client,
                        backend=backend,
                        query=query,
                        strategy=strategy,
                        include_explain=False,
                        enable_reranking=enable_reranking,
                    )
                )
        latencies = [execution.latency_ms for execution in executions]
        benchmark_results[backend] = {
            "summary": summarize_latencies(latencies),
            "queries": [asdict(execution) for execution in executions],
        }
    report = {
        "command": "benchmark",
        "executed_at": _timestamp(),
        "strategy": strategy,
        "enable_reranking": enable_reranking,
        "iterations": iterations,
        "warmup_iterations": warmup_iterations,
        "queries": [asdict(query) for query in queries],
        "backends": benchmark_results,
        "comparison": compare_latency_summaries(benchmark_results),
    }
    return report


def execute_query(
    client: httpx.Client,
    backend: str,
    query: BenchmarkQuery,
    strategy: str,
    include_explain: bool,
    enable_reranking: bool,
) -> QueryExecution:
    """Execute one retrieval request and capture timing plus result metadata."""

    start_time = time.perf_counter()
    response = client.post(
        "/admin/ingestion/search",
        json={
            "query": query.query,
            "top_k": query.top_k,
            "strategy": strategy,
            "index_target": backend,
            "filters": query.filters,
            "include_explain": include_explain,
            "enable_reranking": enable_reranking,
        },
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    response.raise_for_status()
    payload = response.json()
    top_score = payload[0]["score"] if payload else None
    source_backend = payload[0].get("source_backend") if payload else None
    if not payload:
        msg = f"Backend {backend} returned no results for query {query.name}"
        raise RuntimeError(msg)
    if source_backend != backend and not (
        strategy == "hybrid" and source_backend == "hybrid"
    ):
        msg = f"Backend {backend} responded with unexpected source backend {source_backend}"
        raise RuntimeError(msg)
    return QueryExecution(
        backend=backend,
        query_name=query.name,
        latency_ms=elapsed_ms,
        result_count=len(payload),
        top_chunk_ids=[item["chunk_id"] for item in payload],
        top_score=top_score,
        source_backend=source_backend,
    )


def compare_query_results(
    pgvector_execution: QueryExecution,
    qdrant_execution: QueryExecution,
) -> dict[str, Any]:
    """Compare one pgvector and Qdrant result set for overlap and completeness."""

    overlap_rate = compute_overlap_rate(
        pgvector_execution.top_chunk_ids,
        qdrant_execution.top_chunk_ids,
    )
    passed = (
        pgvector_execution.result_count > 0
        and qdrant_execution.result_count > 0
        and overlap_rate > 0.0
    )
    return {
        "query_name": pgvector_execution.query_name,
        "pgvector_result_count": pgvector_execution.result_count,
        "qdrant_result_count": qdrant_execution.result_count,
        "overlap_rate": overlap_rate,
        "pgvector_top_chunk_ids": pgvector_execution.top_chunk_ids,
        "qdrant_top_chunk_ids": qdrant_execution.top_chunk_ids,
        "passed": passed,
    }


def summarize_latencies(latencies_ms: Sequence[float]) -> dict[str, float]:
    """Summarize latency samples into benchmark-friendly percentiles."""

    if not latencies_ms:
        return {
            "count": 0.0,
            "mean_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
        }
    return {
        "count": float(len(latencies_ms)),
        "mean_ms": round(statistics.fmean(latencies_ms), 4),
        "min_ms": round(min(latencies_ms), 4),
        "max_ms": round(max(latencies_ms), 4),
        "p50_ms": round(percentile(latencies_ms, 50), 4),
        "p95_ms": round(percentile(latencies_ms, 95), 4),
        "p99_ms": round(percentile(latencies_ms, 99), 4),
    }


def percentile(samples: Sequence[float], percentile_value: float) -> float:
    """Return a linear-interpolated percentile for the provided samples."""

    ordered_samples = sorted(samples)
    if len(ordered_samples) == 1:
        return ordered_samples[0]
    position = (len(ordered_samples) - 1) * (percentile_value / 100)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered_samples) - 1)
    weight = position - lower_index
    return (
        ordered_samples[lower_index]
        + (ordered_samples[upper_index] - ordered_samples[lower_index]) * weight
    )


def compute_overlap_rate(
    left_chunk_ids: Sequence[str],
    right_chunk_ids: Sequence[str],
) -> float:
    """Return the overlap rate between two ranked chunk id lists."""

    if not left_chunk_ids or not right_chunk_ids:
        return 0.0
    left_set = set(left_chunk_ids)
    right_set = set(right_chunk_ids)
    overlap_count = len(left_set & right_set)
    denominator = min(len(left_set), len(right_set))
    return overlap_count / denominator if denominator else 0.0


def compare_latency_summaries(
    benchmark_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a direct latency comparison across benchmarked backends."""

    pgvector_summary = benchmark_results["pgvector"]["summary"]
    qdrant_summary = benchmark_results["qdrant"]["summary"]
    p50_winner = "pgvector"
    if qdrant_summary["p50_ms"] < pgvector_summary["p50_ms"]:
        p50_winner = "qdrant"
    return {
        "p50_winner": p50_winner,
        "pgvector_p50_ms": pgvector_summary["p50_ms"],
        "qdrant_p50_ms": qdrant_summary["p50_ms"],
        "pgvector_p95_ms": pgvector_summary["p95_ms"],
        "qdrant_p95_ms": qdrant_summary["p95_ms"],
    }


def write_report(
    artifact_dir: Path,
    command_name: str,
    report: dict[str, Any],
) -> Path:
    """Persist a harness report to the artifact directory as JSON."""

    artifact_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact_path = artifact_dir / f"{command_name}-{timestamp}.json"
    artifact_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return artifact_path


def render_report_summary(report: dict[str, Any], artifact_path: Path) -> str:
    """Render a concise human-readable summary for terminal output."""

    lines = [
        f"command: {report['command']}",
        f"executed_at: {report['executed_at']}",
        f"artifact: {artifact_path}",
    ]
    if report["command"] == "integration":
        lines.append(f"status: {report['status']}")
        lines.append(f"comparisons: {len(report['comparisons'])}")
    else:
        lines.append(f"strategy: {report['strategy']}")
        lines.append(
            "p50 winner: "
            f"{report['comparison']['p50_winner']} "
            f"(pgvector={report['comparison']['pgvector_p50_ms']} ms, "
            f"qdrant={report['comparison']['qdrant_p50_ms']} ms)"
        )
    return "\n".join(lines)


def _timestamp() -> str:
    """Return the current UTC timestamp in ISO 8601 form."""

    return datetime.now(tz=UTC).isoformat()


def _workspace_root() -> Path:
    """Return the repository root that contains the Docker Compose stack."""

    return Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    main()
