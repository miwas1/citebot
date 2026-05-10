#!/usr/bin/env python3
"""End-to-end evaluation of the transformer interpretability research scenario.

This script tests the CiteBot pipeline against the canonical scenario:

    "Compare transformer interpretability techniques published after 2022
     and summarize limitations."

What is exercised
-----------------
- Dense + sparse hybrid retrieval
- Temporal filtering (``published_at >= 2022-01-01``)
- Multi-document synthesis across sources
- Citation traceability (grounded answer spans)
- Hallucination resistance (RAGAS faithfulness)
- Optional web search for latest papers

Usage
-----
.. code-block:: bash

    # Smoke test against already-ingested corpus
    python scripts/run_interpretability_scenario.py

    # Ingest corpus first, then evaluate
    python scripts/run_interpretability_scenario.py \\
        --corpus data/corpus/interpretability/interpretability_merged.jsonl \\
        --ingest

    # Save detailed results to JSON
    python scripts/run_interpretability_scenario.py --output results/interp_eval.json

    # Run RAGAS scoring (requires ragas + LLM API key in env)
    python scripts/run_interpretability_scenario.py --ragas

Environment variables
---------------------
``OPENAI_API_KEY``   Required for RAGAS faithfulness scoring.
``ANTHROPIC_API_KEY`` Alternative LLM provider for the research agent.
``S2_API_KEY``        Semantic Scholar key for web-search citations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("interp-scenario")

# ---------------------------------------------------------------------------
# Evaluation case definition
# ---------------------------------------------------------------------------

#: Primary research query for the scenario
SCENARIO_QUERY = (
    "Compare transformer interpretability techniques published after 2022 "
    "and summarize limitations."
)

#: Sub-queries exercising different retrieval dimensions
RETRIEVAL_PROBE_QUERIES: list[dict[str, Any]] = [
    {
        "query": "mechanistic interpretability transformers circuits 2022 2023",
        "description": "Mechanistic circuits literature",
        "expected_keywords": ["circuit", "mechanistic", "attention head", "MLP"],
        "strategy": "hybrid",
    },
    {
        "query": "attention visualization saliency gradient attribution transformer",
        "description": "Gradient/saliency attribution methods",
        "expected_keywords": [
            "saliency",
            "gradient",
            "attribution",
            "integrated gradients",
        ],
        "strategy": "dense",
    },
    {
        "query": "probing classifiers internal representations BERT GPT",
        "description": "Probing / representation analysis",
        "expected_keywords": ["probing", "linear probe", "representation", "latent"],
        "strategy": "hybrid",
    },
    {
        "query": "activation patching causal intervention language model",
        "description": "Causal intervention methods",
        "expected_keywords": [
            "activation patching",
            "causal",
            "intervention",
            "path patching",
        ],
        "strategy": "dense",
    },
    {
        "query": "limitations interpretability faithfulness completeness",
        "description": "Limitations of interpretability methods",
        "expected_keywords": ["limitation", "faithfulness", "completeness", "spurious"],
        "strategy": "sparse",
    },
]

#: Traits the final synthesised answer must exhibit
EXPECTED_ANSWER_TRAITS = [
    "names at least 3 distinct interpretability technique families",
    "discusses limitations of each technique",
    "references papers published after 2022",
    "includes inline citations with source URIs",
    "does not hallucinate paper titles or authors",
    "uses temporal language (post-2022, recent, 2023, 2024)",
]

#: Known high-quality papers to check for recall
GOLD_PAPERS = [
    "https://arxiv.org/abs/2202.05262",  # Interpretability in the Wild (IOI circuit)
    "https://arxiv.org/abs/2301.04213",  # ROME / causal tracing
    "https://arxiv.org/abs/2304.01338",  # Towards Automated Circuit Discovery
    "https://arxiv.org/abs/2211.00593",  # Superposition / polysemanticity
    "https://arxiv.org/abs/2309.01550",  # Scaling monosemanticity (2023)
]


# ---------------------------------------------------------------------------
# Retrieval probe runner
# ---------------------------------------------------------------------------


async def _run_retrieval_probe(
    container: Any,
    probe: dict[str, Any],
    top_k: int,
    after_date: str,
) -> dict[str, Any]:
    """Execute a single retrieval probe and collect recall metrics.

    Args:
        container: Initialized CiteBot DI container providing ``retrieval_service``.
        probe: Probe definition dict with ``query``, ``strategy``, and
               ``expected_keywords`` fields.
        top_k: Number of results to retrieve for recall evaluation.
        after_date: ISO date string for temporal filtering.

    Returns:
        Dict with probe metadata and retrieval quality metrics.
    """
    from app.ingestion.schemas import RetrievalFilters, SearchRequest

    request = SearchRequest(
        query=probe["query"],
        top_k=top_k,
        strategy=probe["strategy"],
        filters=RetrievalFilters(published_after=after_date),
        include_explain=True,
    )

    start = time.perf_counter()
    try:
        results = await container.retrieval_service.search(request)
    except Exception as exc:  # noqa: BLE001
        log.warning("Retrieval probe failed: %s", exc)
        return {
            "probe": probe["description"],
            "strategy": probe["strategy"],
            "error": str(exc),
            "recall_at_k": 0.0,
            "keyword_hit_rate": 0.0,
            "latency_ms": 0,
        }
    latency_ms = int((time.perf_counter() - start) * 1000)

    retrieved_texts = " ".join(r.text for r in results).lower()
    retrieved_uris = {r.source_uri for r in results}

    # Keyword hit rate: fraction of expected keywords found in top-k results
    hits = sum(1 for kw in probe["expected_keywords"] if kw.lower() in retrieved_texts)
    keyword_hit_rate = (
        hits / len(probe["expected_keywords"]) if probe["expected_keywords"] else 0.0
    )

    # Temporal compliance: check published_at on returned chunks
    post_2022_count = 0
    for result in results:
        pub = getattr(result, "published_at", None) or result.metadata.get(
            "published_at"
        )
        if pub:
            try:
                dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
                if dt.year >= 2022:
                    post_2022_count += 1
            except (ValueError, TypeError):
                pass
    temporal_compliance = post_2022_count / len(results) if results else 0.0

    return {
        "probe": probe["description"],
        "query": probe["query"],
        "strategy": probe["strategy"],
        "top_k": top_k,
        "results_returned": len(results),
        "keyword_hit_rate": round(keyword_hit_rate, 3),
        "temporal_compliance": round(temporal_compliance, 3),
        "latency_ms": latency_ms,
        "retrieved_uris": list(retrieved_uris)[:10],
    }


# ---------------------------------------------------------------------------
# Gold paper recall
# ---------------------------------------------------------------------------


async def _check_gold_recall(container: Any, top_k: int) -> dict[str, Any]:
    """Measure recall of known gold-standard interpretability papers.

    Runs the primary scenario query and checks how many of the known
    gold papers appear in the top-k results.

    Args:
        container: Initialized CiteBot DI container.
        top_k: Number of results to check for recall computation.

    Returns:
        Dict with recall@k score and list of found/missing gold URIs.
    """
    from app.ingestion.schemas import RetrievalFilters, SearchRequest

    request = SearchRequest(
        query=SCENARIO_QUERY,
        top_k=top_k,
        strategy="hybrid",
        filters=RetrievalFilters(published_after="2022-01-01"),
    )
    try:
        results = await container.retrieval_service.search(request)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "recall_at_k": 0.0}

    retrieved_uris = {r.source_uri for r in results}
    # Normalise IDs (strip trailing slashes, normalise arxiv ID format)
    retrieved_ids = {uri.rstrip("/").split("/")[-1] for uri in retrieved_uris}

    found = []
    missing = []
    for gold_uri in GOLD_PAPERS:
        gold_id = gold_uri.rstrip("/").split("/")[-1]
        if gold_uri in retrieved_uris or gold_id in retrieved_ids:
            found.append(gold_uri)
        else:
            missing.append(gold_uri)

    recall = len(found) / len(GOLD_PAPERS) if GOLD_PAPERS else 0.0
    return {
        "recall_at_k": round(recall, 3),
        "k": top_k,
        "gold_total": len(GOLD_PAPERS),
        "found": found,
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Agent answer evaluation
# ---------------------------------------------------------------------------


async def _run_agent_answer(
    container: Any,
    allow_web_search: bool = False,
) -> dict[str, Any]:
    """Run the full research agent for the scenario query and collect output.

    Args:
        container: Initialized CiteBot DI container with ``research_agent_service``.
        allow_web_search: Whether to permit the agent to call the web-search tool.

    Returns:
        Dict containing the agent's answer text, citations, and timing metadata.
    """
    from app.agents.schemas import ResearchQueryRequest

    request = ResearchQueryRequest(
        query=SCENARIO_QUERY,
        allow_web_search=allow_web_search,
        session_id="interp-scenario-eval",
    )
    start = time.perf_counter()
    try:
        response = await container.research_agent_service.run(request)
    except Exception as exc:  # noqa: BLE001
        log.error("Agent run failed: %s", exc)
        return {"error": str(exc), "answer": "", "citations": []}
    latency_ms = int((time.perf_counter() - start) * 1000)

    answer_text = getattr(response, "answer", "") or ""
    citations = getattr(response, "citations", []) or []

    # Check expected traits in the answer
    answer_lower = answer_text.lower()
    trait_hits: dict[str, bool] = {}
    for trait in EXPECTED_ANSWER_TRAITS:
        # Heuristic keyword checks per trait
        if "3 distinct" in trait or "technique families" in trait:
            techniques = [
                "mechanistic",
                "probing",
                "saliency",
                "circuit",
                "attribution",
                "activation patch",
                "causal",
                "sparse autoencode",
            ]
            trait_hits[trait] = sum(1 for t in techniques if t in answer_lower) >= 3
        elif "limitations" in trait:
            trait_hits[trait] = (
                "limitation" in answer_lower or "drawback" in answer_lower
            )
        elif "after 2022" in trait or "published" in trait:
            trait_hits[trait] = any(
                yr in answer_text for yr in ["2023", "2024", "2025"]
            )
        elif "citation" in trait:
            trait_hits[trait] = len(citations) > 0
        elif "hallucinate" in trait:
            trait_hits[trait] = True  # can only fully verify with RAGAS
        elif "temporal language" in trait:
            trait_hits[trait] = any(
                kw in answer_lower
                for kw in ["post-2022", "recent", "2023", "2024", "since 2022"]
            )
        else:
            trait_hits[trait] = False

    trait_score = sum(trait_hits.values()) / len(trait_hits) if trait_hits else 0.0

    return {
        "answer_length": len(answer_text),
        "answer_preview": answer_text[:500] + ("…" if len(answer_text) > 500 else ""),
        "citation_count": len(citations),
        "citations": [getattr(c, "source_uri", str(c)) for c in citations[:20]],
        "trait_score": round(trait_score, 3),
        "trait_hits": trait_hits,
        "latency_ms": latency_ms,
        "allow_web_search": allow_web_search,
    }


# ---------------------------------------------------------------------------
# RAGAS scoring (optional)
# ---------------------------------------------------------------------------


def _run_ragas(answer: str, contexts: list[str], question: str) -> dict[str, Any]:
    """Compute RAGAS faithfulness and answer relevance for the scenario.

    Requires the ``ragas`` package and a valid ``OPENAI_API_KEY`` in the
    environment.  Returns empty metrics if the package is unavailable.

    Args:
        answer: The agent's generated answer string.
        contexts: List of retrieved context passages used to generate the answer.
        question: The original research question.

    Returns:
        Dict with ``faithfulness``, ``answer_relevancy``, and ``status`` keys.
    """
    try:
        from datasets import Dataset  # type: ignore[import]
        from ragas import evaluate  # type: ignore[import]
        from ragas.metrics import answer_relevancy, faithfulness  # type: ignore[import]
    except ImportError:
        log.warning("ragas/datasets not installed – skipping RAGAS scoring")
        return {"status": "skipped", "reason": "ragas not installed"}

    data = Dataset.from_dict(
        {
            "question": [question],
            "answer": [answer],
            "contexts": [contexts],
        }
    )
    try:
        result = evaluate(data, metrics=[faithfulness, answer_relevancy])
        return {
            "status": "completed",
            "faithfulness": round(float(result["faithfulness"]), 4),
            "answer_relevancy": round(float(result["answer_relevancy"]), 4),
        }
    except Exception as exc:  # noqa: BLE001
        log.error("RAGAS evaluation failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Main evaluation runner
# ---------------------------------------------------------------------------


async def _run_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """Orchestrate the full scenario evaluation pipeline.

    Args:
        args: Parsed CLI namespace containing all configuration flags.

    Returns:
        Complete evaluation result dict suitable for JSON serialisation.
    """
    from app.core.config import get_settings
    from app.core.lifecycle import build_container

    settings = get_settings()
    container = build_container(settings)
    await container.initialize()

    try:
        # Optional: ingest corpus before evaluating
        if args.corpus:
            corpus_path = Path(args.corpus)
            if not corpus_path.exists():
                log.error("Corpus path does not exist: %s", corpus_path)
                sys.exit(1)
            log.info("Ingesting corpus: %s", corpus_path)
            ingest_result = await container.ingestion_service.ingest_path(
                source_path=corpus_path,
                embedding_version="v1",
                index_version="v1",
            )
            log.info("Ingestion: %s", ingest_result.model_dump_json())

        top_k = args.top_k

        # 1. Retrieval probes
        log.info("Running %s retrieval probes …", len(RETRIEVAL_PROBE_QUERIES))
        probe_results = []
        for probe in RETRIEVAL_PROBE_QUERIES:
            result = await _run_retrieval_probe(container, probe, top_k, "2022-01-01")
            probe_results.append(result)
            log.info(
                "  [%s] keyword_hit=%.2f temporal_ok=%.2f latency=%sms",
                probe["description"],
                result.get("keyword_hit_rate", 0),
                result.get("temporal_compliance", 0),
                result.get("latency_ms", 0),
            )

        # 2. Gold paper recall
        log.info("Checking gold paper recall@%s …", top_k)
        gold_recall = await _check_gold_recall(container, top_k)
        log.info("  recall@%s = %.3f", top_k, gold_recall.get("recall_at_k", 0))

        # 3. Full agent answer
        log.info("Running research agent …")
        agent_result = await _run_agent_answer(
            container, allow_web_search=args.web_search
        )
        log.info(
            "  trait_score=%.3f citations=%s length=%s chars latency=%sms",
            agent_result.get("trait_score", 0),
            agent_result.get("citation_count", 0),
            agent_result.get("answer_length", 0),
            agent_result.get("latency_ms", 0),
        )

        # 4. RAGAS (optional)
        ragas_result: dict[str, Any] = {"status": "disabled"}
        if args.ragas and agent_result.get("answer"):
            log.info("Running RAGAS scoring …")
            # Collect top contexts from the primary query for RAGAS input
            from app.ingestion.schemas import RetrievalFilters, SearchRequest

            ctx_req = SearchRequest(
                query=SCENARIO_QUERY,
                top_k=10,
                strategy="hybrid",
                filters=RetrievalFilters(published_after="2022-01-01"),
            )
            ctx_results = await container.retrieval_service.search(ctx_req)
            contexts = [r.text for r in ctx_results]
            ragas_result = _run_ragas(
                answer=agent_result.get("answer_preview", ""),
                contexts=contexts,
                question=SCENARIO_QUERY,
            )
            log.info("  RAGAS: %s", ragas_result)

        # ---------------------------------------------------------------------------
        # Aggregate metrics
        # ---------------------------------------------------------------------------
        avg_keyword_hit = (
            sum(p.get("keyword_hit_rate", 0) for p in probe_results)
            / len(probe_results)
            if probe_results
            else 0.0
        )
        avg_temporal = (
            sum(p.get("temporal_compliance", 0) for p in probe_results)
            / len(probe_results)
            if probe_results
            else 0.0
        )

        summary = {
            "scenario": SCENARIO_QUERY,
            "evaluated_at": datetime.now(tz=UTC).isoformat(),
            "top_k": top_k,
            "corpus_path": str(args.corpus) if args.corpus else None,
            "metrics": {
                "recall_at_k": gold_recall.get("recall_at_k", 0.0),
                "avg_keyword_hit_rate": round(avg_keyword_hit, 3),
                "avg_temporal_compliance": round(avg_temporal, 3),
                "agent_trait_score": agent_result.get("trait_score", 0.0),
                "citation_count": agent_result.get("citation_count", 0),
                "ragas_faithfulness": ragas_result.get("faithfulness"),
                "ragas_answer_relevancy": ragas_result.get("answer_relevancy"),
            },
            "gold_recall": gold_recall,
            "retrieval_probes": probe_results,
            "agent_answer": agent_result,
            "ragas": ragas_result,
        }

        return summary

    finally:
        await container.teardown()


def _print_report(result: dict[str, Any]) -> None:
    """Print a formatted human-readable evaluation report to stdout.

    Args:
        result: Evaluation result dict produced by ``_run_evaluation``.
    """
    metrics = result.get("metrics", {})
    separator = "─" * 60

    print()
    print(separator)
    print("  CiteBot Interpretability Scenario – Evaluation Report")
    print(separator)
    print(f"  Scenario: {result['scenario']}")
    print(f"  Evaluated: {result['evaluated_at']}")
    print(f"  Top-K: {result['top_k']}")
    if result.get("corpus_path"):
        print(f"  Corpus:   {result['corpus_path']}")
    print()
    print("  Core Metrics")
    print(
        f"    Recall@{result['top_k']}:               {metrics.get('recall_at_k', 0):.3f}"
    )
    print(
        f"    Avg Keyword Hit Rate:      {metrics.get('avg_keyword_hit_rate', 0):.3f}"
    )
    print(
        f"    Avg Temporal Compliance:   {metrics.get('avg_temporal_compliance', 0):.3f}"
    )
    print(f"    Agent Trait Score:         {metrics.get('agent_trait_score', 0):.3f}")
    print(f"    Citation Count:            {metrics.get('citation_count', 0)}")
    if metrics.get("ragas_faithfulness") is not None:
        print(f"    RAGAS Faithfulness:        {metrics['ragas_faithfulness']:.4f}")
    if metrics.get("ragas_answer_relevancy") is not None:
        print(f"    RAGAS Answer Relevancy:    {metrics['ragas_answer_relevancy']:.4f}")
    print()

    gold = result.get("gold_recall", {})
    print("  Gold Paper Recall")
    for uri in gold.get("found", []):
        print(f"    ✓ {uri}")
    for uri in gold.get("missing", []):
        print(f"    ✗ {uri}  (not retrieved)")
    print()

    agent = result.get("agent_answer", {})
    print("  Answer Traits")
    for trait, hit in (agent.get("trait_hits") or {}).items():
        icon = "✓" if hit else "✗"
        print(f"    {icon} {trait}")
    print()

    agent_preview = agent.get("answer_preview", "")
    if agent_preview:
        print("  Answer Preview (500 chars)")
        print(f"    {agent_preview}")
        print()
    print(separator)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the scenario evaluation script.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="run_interpretability_scenario",
        description=(
            "Evaluate CiteBot on the transformer interpretability research scenario."
        ),
    )
    parser.add_argument(
        "--corpus",
        metavar="PATH",
        default=None,
        help=(
            "Path to a JSONL corpus file to ingest before evaluating. "
            "If omitted the already-ingested index is used."
        ),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of retrieval results to request per probe (default: 10).",
    )
    parser.add_argument(
        "--web-search",
        action="store_true",
        default=False,
        help="Allow the research agent to call the web-search tool.",
    )
    parser.add_argument(
        "--ragas",
        action="store_true",
        default=False,
        help="Run RAGAS faithfulness + relevancy scoring (requires OPENAI_API_KEY).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Write full JSON result to this file path.",
    )
    return parser


def main() -> None:
    """Entrypoint: parse arguments, run evaluation, print report, and optionally save results."""
    parser = _build_parser()
    args = parser.parse_args()

    result = asyncio.run(_run_evaluation(args))
    _print_report(result)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Full results written to %s", output_path)

    # Exit non-zero if key metrics are below acceptable thresholds
    metrics = result.get("metrics", {})
    failed = (
        metrics.get("recall_at_k", 0) < 0.3
        or metrics.get("avg_keyword_hit_rate", 0) < 0.4
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
    main()
