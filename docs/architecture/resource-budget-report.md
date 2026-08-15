# Low-resource release budget

This is the operating budget for the per-user or small-team profile. The values
are ceilings to validate during a host-specific soak run, not performance claims.

| Resource | `core-low-resource` ceiling | Control |
| --- | ---: | --- |
| Host memory | 16 GB total; keep 2 GB free | one generation, bounded retrieval, no concurrent OCR |
| Generation concurrency | 1 | `LLM_GENERATION_CONCURRENCY=1` |
| OCR/layout concurrency | 1 page/job | `OCR_CONCURRENCY=1`, `OCR_MAX_PAGES` |
| Reranking candidates | 40 | bounded reranker candidate pool |
| NLI pairs per answer | 32 | `VERIFICATION_MAX_PAIRS` |
| Query decomposition | 4 subqueries | retrieval planner bound |
| Evidence anchors per material claim | 10 | selector bound |
| Source upload | 25 MiB/request by default | `MAX_INPUT_BYTES` |

The default profile uses native parsing, sparse/local retrieval, a bounded
heuristic reranker, deterministic verification, and a local answer generator.
Docling/PP-Structure, compact NLI, deep retrieval, and tabular calculation are
opt-in or sequential so that peak memory does not become the sum of every model.

## Required soak measurements

Before promoting a model or parser, run the evaluation and record peak RSS,
latency by stage, OCR pages/minute, index size, swap activity, OOM/restart count,
and quality deltas for every dataset family. A release fails if the host loses
the 2 GB reserve, sustains swap, exceeds a configured timeout, or regresses a
critical quality gate even when the aggregate score improves.

The repository exposes stage timings in `ResearchResponse.stage_timings_ms` and
persists evaluation artifacts under the configured evaluation artifact directory.
The measurements still need to be collected on the target 16 GB host with the
selected local models; this document deliberately does not present modeled
estimates as measured results.
