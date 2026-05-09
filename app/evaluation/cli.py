"""CLI entry points for running and inspecting evaluation artifacts."""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import get_settings
from app.core.lifecycle import build_container
from app.evaluation.schemas import EvaluationRunRequest


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for evaluation operations."""

    parser = argparse.ArgumentParser(description="Run CiteBot evaluation workflows.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--dataset-path")
    run_parser.add_argument("--source-path")
    run_parser.add_argument("--force-reindex", action="store_true")
    run_parser.add_argument("--embedding-version", default="default")
    run_parser.add_argument("--index-version", default="default")
    run_parser.add_argument("--run-ragas", action="store_true")
    run_parser.add_argument(
        "--threshold-mode",
        choices=["report", "ci"],
        default="report",
    )

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("run_id")
    return parser


def main() -> None:
    """Execute the evaluation CLI and exit with an appropriate status code."""

    raise SystemExit(asyncio.run(_run_cli()))


async def _run_cli() -> int:
    """Run the selected evaluation command inside an initialized container."""

    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()
    container = build_container(settings)
    await container.initialize()
    try:
        if args.command == "run":
            result = await container.evaluation_service.run(
                EvaluationRunRequest(
                    dataset_path=args.dataset_path,
                    source_path=args.source_path,
                    force_reindex=args.force_reindex,
                    embedding_version=args.embedding_version,
                    index_version=args.index_version,
                    run_ragas=args.run_ragas,
                    threshold_mode=args.threshold_mode,
                )
            )
            print(result.model_dump_json(indent=2))
            return 1 if result.status == "failed" and args.threshold_mode == "ci" else 0
        result = await container.evaluation_service.get_run(args.run_id)
        if result is None:
            print(f"Evaluation run not found: {args.run_id}")
            return 1
        print(result.model_dump_json(indent=2))
        return 0
    finally:
        await container.close()
