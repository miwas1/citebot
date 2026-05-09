"""Command-line entrypoint for ingestion and local search workflows."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.core.config import get_settings
from app.core.lifecycle import build_container
from app.ingestion.schemas import RetrievalFilters, SearchRequest


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for ingestion and search commands."""

    parser = argparse.ArgumentParser(description="Manage CiteBot ingestion jobs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest a corpus path.")
    ingest_parser.add_argument("source_path", type=Path)
    ingest_parser.add_argument("--embedding-version", default="v1")
    ingest_parser.add_argument("--index-version", default="v1")

    reindex_parser = subparsers.add_parser(
        "reindex", help="Force re-index a corpus path."
    )
    reindex_parser.add_argument("source_path", type=Path)
    reindex_parser.add_argument("--embedding-version", default="v1")
    reindex_parser.add_argument("--index-version", default="v1")

    search_parser = subparsers.add_parser(
        "search", help="Search dense, sparse, or hybrid retrieval indexes."
    )
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=5)
    search_parser.add_argument(
        "--strategy",
        choices=["sparse", "dense", "hybrid"],
        default="hybrid",
    )
    search_parser.add_argument(
        "--index-target",
        choices=["auto", "pgvector", "qdrant", "local"],
        default="auto",
    )
    search_parser.add_argument("--document-id", action="append", default=[])
    search_parser.add_argument("--source-uri", action="append", default=[])
    search_parser.add_argument("--access-policy", action="append", default=[])
    search_parser.add_argument("--embedding-version")
    search_parser.add_argument("--index-version")
    search_parser.add_argument("--include-explain", action="store_true")
    search_parser.add_argument("--disable-reranking", action="store_true")
    return parser


async def run_cli() -> int:
    """Execute the requested ingestion or search command."""

    parser = build_parser()
    args = parser.parse_args()
    container = build_container(get_settings())
    await container.initialize()
    try:
        if args.command == "ingest":
            result = await container.ingestion_service.ingest_path(
                source_path=args.source_path,
                embedding_version=args.embedding_version,
                index_version=args.index_version,
            )
            print(result.model_dump_json(indent=2))
        elif args.command == "reindex":
            result = await container.ingestion_service.reindex_path(
                source_path=args.source_path,
                embedding_version=args.embedding_version,
                index_version=args.index_version,
            )
            print(result.model_dump_json(indent=2))
        else:
            search_request = SearchRequest(
                query=args.query,
                top_k=args.top_k,
                strategy=args.strategy,
                index_target=args.index_target,
                filters=RetrievalFilters(
                    document_ids=list(args.document_id),
                    source_uris=list(args.source_uri),
                    access_policies=list(args.access_policy),
                    embedding_version=args.embedding_version,
                    index_version=args.index_version,
                ),
                include_explain=args.include_explain,
                enable_reranking=False if args.disable_reranking else None,
            )
            results = await container.retrieval_service.search(search_request)
            rendered_results = ",\n".join(
                result.model_dump_json(indent=2) for result in results
            )
            print("[\n" + rendered_results + "\n]")
    finally:
        await container.close()
    return 0


def main() -> None:
    """Run the ingestion CLI inside an asyncio event loop."""

    raise SystemExit(asyncio.run(run_cli()))


if __name__ == "__main__":
    main()
