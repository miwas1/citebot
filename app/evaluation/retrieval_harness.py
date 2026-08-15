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
            default=["postgres", "embedding", "llm", "api", "document-worker"],
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
    if payload.get("status") in {"queued", "running"}:
        job_id = payload.get("job_id")
        deadline = time.monotonic() + 300.0
        while job_id and time.monotonic() < deadline:
            time.sleep(1.0)
            status_response = client.get(f"/admin/ingestion/jobs/{job_id}")
            status_response.raise_for_status()
            payload = status_response.json()
            if payload.get("status") in {"completed", "failed", "quarantined"}:
                break
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
    """Run pgvector integration checks against the deployed API."""

    executions = [
        execute_query(
            client,
            backend="pgvector",
            query=query,
            strategy="dense",
            include_explain=True,
            enable_reranking=False,
        )
        for query in queries
    ]
    return {
        "command": "integration",
        "executed_at": _timestamp(),
        "queries": [asdict(query) for query in queries],
        "backend": "pgvector",
        "executions": [asdict(execution) for execution in executions],
        "status": "passed",
    }


def run_benchmark_suite(
    client: httpx.Client,
    queries: Sequence[BenchmarkQuery],
    iterations: int,
    warmup_iterations: int,
    strategy: str,
    enable_reranking: bool,
) -> dict[str, Any]:
    """Benchmark pgvector retrieval latency through the API."""

    for _ in range(warmup_iterations):
        for query in queries:
            execute_query(
                client,
                backend="pgvector",
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
                    backend="pgvector",
                    query=query,
                    strategy=strategy,
                    include_explain=False,
                    enable_reranking=enable_reranking,
                )
            )
    latencies = [execution.latency_ms for execution in executions]
    return {
        "command": "benchmark",
        "executed_at": _timestamp(),
        "backend": "pgvector",
        "strategy": strategy,
        "enable_reranking": enable_reranking,
        "iterations": iterations,
        "warmup_iterations": warmup_iterations,
        "queries": [asdict(query) for query in queries],
        "summary": summarize_latencies(latencies),
        "executions": [asdict(execution) for execution in executions],
    }


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
        lines.append(f"queries: {len(report['executions'])}")
    else:
        lines.append(f"strategy: {report['strategy']}")
        lines.append(f"pgvector p50: {report['summary']['p50_ms']} ms")
    return "\n".join(lines)


def _timestamp() -> str:
    """Return the current UTC timestamp in ISO 8601 form."""

    return datetime.now(tz=UTC).isoformat()


def _workspace_root() -> Path:
    """Return the repository root that contains the Docker Compose stack."""

    return Path(__file__).resolve().parents[2]


if __name__ == "__main__":
    main()
