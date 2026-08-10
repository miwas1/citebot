"""Run a production-shaped 16 GB CiteBot soak and record host evidence."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_QUERIES = (
    "What does citation traceability depend on?",
    "How should retrieval quality be evaluated?",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the host soak command-line interface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--duration-seconds", type=float, default=1800.0)
    parser.add_argument("--query-interval-seconds", type=float, default=10.0)
    parser.add_argument("--stats-interval-seconds", type=float, default=5.0)
    parser.add_argument("--ingest-source-path", default=None)
    parser.add_argument("--compose-up", action="store_true")
    parser.add_argument("--compose-down", action="store_true")
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=Path("artifacts/benchmarks/16gb-soak.json"),
    )
    return parser


def run(argv: list[str] | None = None) -> int:
    """Run the soak and write a JSON report."""

    args = build_parser().parse_args(argv)
    if args.duration_seconds <= 0 or args.query_interval_seconds <= 0:
        raise SystemExit("duration and query interval must be positive")
    compose_started = False
    if args.compose_up:
        run_command(["docker", "compose", "up", "-d"])
        compose_started = True

    started_at = datetime.now(UTC).isoformat()
    report: dict[str, Any] = {
        "started_at": started_at,
        "duration_seconds": args.duration_seconds,
        "api_base_url": args.api_base_url,
        "queries": [],
        "docker_stats": [],
        "errors": [],
    }
    try:
        with httpx.Client(base_url=args.api_base_url, timeout=120.0) as client:
            wait_until_ready(client, timeout_seconds=180.0)
            if args.ingest_source_path:
                report["ingestion"] = enqueue_ingestion(client, args.ingest_source_path)
            run_loop(
                client,
                report,
                duration_seconds=args.duration_seconds,
                query_interval_seconds=args.query_interval_seconds,
                stats_interval_seconds=args.stats_interval_seconds,
            )
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_report(args.artifact_path, report)
        if compose_started and args.compose_down:
            run_command(["docker", "compose", "down"])
    return 0 if not report["errors"] else 1


def wait_until_ready(client: httpx.Client, timeout_seconds: float) -> None:
    """Wait for the dependency-aware readiness endpoint."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = client.get("/ready")
            if response.is_success and response.json().get("status") == "ready":
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1.0)
    raise RuntimeError("Timed out waiting for CiteBot readiness")


def enqueue_ingestion(client: httpx.Client, source_path: str) -> dict[str, Any]:
    """Submit one queued ingestion job and return its initial status."""

    response = client.post("/admin/ingestion/jobs", json={"source_path": source_path})
    response.raise_for_status()
    return response.json()


def run_loop(
    client: httpx.Client,
    report: dict[str, Any],
    duration_seconds: float,
    query_interval_seconds: float,
    stats_interval_seconds: float,
) -> None:
    """Issue serial research queries while sampling Docker resource usage."""

    deadline = time.monotonic() + duration_seconds
    next_query = time.monotonic()
    next_stats = next_query
    query_index = 0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_stats:
            report["docker_stats"].append(read_docker_stats())
            next_stats = now + stats_interval_seconds
        if now >= next_query:
            query = DEFAULT_QUERIES[query_index % len(DEFAULT_QUERIES)]
            query_index += 1
            started = time.perf_counter()
            try:
                response = client.post(
                    "/research/query",
                    json={"query": query, "top_k": 5},
                )
                latency_ms = (time.perf_counter() - started) * 1000
                response.raise_for_status()
                report["queries"].append(
                    {
                        "query": query,
                        "latency_ms": round(latency_ms, 2),
                        "status_code": response.status_code,
                    }
                )
            except (httpx.HTTPError, ValueError) as error:
                report["errors"].append({"stage": "query", "error": str(error)})
            next_query = now + query_interval_seconds
        time.sleep(min(0.25, max(0.01, min(next_query, next_stats) - time.monotonic())))

    report["query_latency_summary"] = summarize(
        [item["latency_ms"] for item in report["queries"]]
    )


def read_docker_stats() -> dict[str, Any]:
    """Read one no-stream Docker stats sample for all CiteBot containers."""

    try:
        completed = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}
    samples = []
    for line in completed.stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "citebot" in str(payload.get("Name", "")).lower():
            samples.append(payload)
    return {"captured_at": datetime.now(UTC).isoformat(), "containers": samples}


def summarize(values: list[float]) -> dict[str, float]:
    """Summarize latency samples for quick release-gate review."""

    if not values:
        return {"count": 0.0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}
    ordered = sorted(values)
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 2),
        "p50_ms": round(_percentile(ordered, 50), 2),
        "p95_ms": round(_percentile(ordered, 95), 2),
    }


def _percentile(values: list[float], percentile: float) -> float:
    """Return a linear-interpolated percentile."""

    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Persist the benchmark artifact and create its parent directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def run_command(command: list[str]) -> None:
    """Run a required host command with a readable error."""

    try:
        subprocess.run(command, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"Command failed: {' '.join(command)}") from error


if __name__ == "__main__":
    raise SystemExit(run())
