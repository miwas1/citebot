# Backend Architecture Assessment

## Executive summary
CiteBot is a CPU-only, local-first RAG modular monolith deployed as five always-on Compose services: FastAPI, one ingestion worker, a Qwen3-Embedding-0.6B server, a Phi-4-mini Q4 llama.cpp server, and Qdrant. SQLite, the SQLite FTS5 sparse index, source documents, and model artifacts remain local. The 16 GB profile now has explicit per-service CPU/RAM limits, a 4,096-token context default, bounded research admission, bounded ingestion, and automatic local dense fallback disabled in Compose. It is appropriate for a single user or very small trusted workgroup, subject to the host soak benchmark still being run with the real model artifacts.

The recommended operating envelope is one answer generation at a time, one ingestion job at a time, no simultaneous bulk OCR and interactive generation, a 4,096-token initial LLM context, embedding batches of 4-8, and a corpus below roughly 250,000 1,024-dimensional chunks until measurements justify more. The machine should retain at least 2-3 GB of available memory under peak activity and avoid sustained swap. A hard 14 GB aggregate container ceiling, host-level OOM monitoring, and service-specific CPU/thread limits should be added before treating the deployment as reliable.

The highest-impact implementation work is complete: directory/JSONL ingestion streams under byte/document budgets, sparse retrieval uses transactional SQLite FTS5, Qdrant failure no longer triggers an unbounded local re-embedding scan in Compose, and expensive research requests have bounded admission. The remaining release gate is a real-model Docker soak benchmark and restart/reconciliation testing. No rewrite is warranted.

Planning estimates below are deliberately ranges because the repository does not include provisioned model weights or runtime measurements. Actual resident memory depends on the Core i7 generation/instruction set, llama.cpp build, model artifact, context length, corpus size, and Docker/OS overhead.

## System overview
- Primary language/framework: Python 3.11 container runtime, FastAPI, Pydantic Settings, async SQLAlchemy, LangGraph, PyMuPDF/PaddleOCR, Qdrant, text-embeddings-inference, and llama.cpp (`Dockerfile`, `pyproject.toml`, `app/main.py:create_app`).
- Topology: modular monolith split into API and ingestion-worker processes, with separate local model and vector services; optional PostgreSQL/pgvector and Dozzle profiles (`docker-compose.yml`, `app/core/lifecycle.py:build_container`).
- Request lifecycle: HTTP -> observability/auth/rate middleware -> FastAPI route -> shared service container -> retrieval/agent/evaluation service -> SQLite/filesystem/Qdrant/local model HTTP (`app/main.py:create_app`, `app/core/lifecycle.py:build_container`).
- Background processing model: a durable SQLite job table is polled by one sequential worker; each claimed job runs load -> normalize -> chunk -> embed -> persist -> vector/sparse writes (`app/ingestion/worker.py:run_worker`, `app/ingestion/service.py:_process_job`).
- Deployment model: Docker Compose with only the API published on loopback; model and data services use an internal network. The default stack sets per-service memory/CPU limits and restart policies (`docker-compose.yml`).
- Confidence: high for code and Compose topology; medium for hardware capacity because no representative models/corpus were provisioned and no live Compose benchmark could be run in this environment.

## Component inventory

| Component | Type | Responsibility | Key files | Owned state | Dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| API | Runtime/API | Research, ingestion administration, evaluation, health, metrics | `app/main.py`, `app/api/routes/`, `app/core/lifecycle.py` | In-process metrics, rate buckets, service graph | Every application service | High |
| Document worker | Runtime/worker | Claims durable jobs and runs one ingestion pipeline | `app/ingestion/worker.py`, `service.py`, `repository.py` | Job leases/progress in SQLite | Parser/OCR, embedding, Qdrant, sparse index | High |
| Local loader/OCR | CPU/RAM-heavy adapter | Loads text, JSON, JSONL, PDF, DOCX, images; selectively OCRs | `app/ingestion/loaders.py`, `ocr.py` | Structured documents and temporary page images | PyMuPDF, PaddleOCR, Tesseract | High |
| Embedding service | Model service | Produces 1,024-dimensional Qwen3 embeddings in bounded HTTP batches | `app/ingestion/embedder.py`, `docker-compose.yml:embedding` | Model weights/runtime cache | TEI CPU image, local model files | High |
| Answer service | Model service | Generates grounded JSON answers through llama.cpp | `app/agents/generation.py:LlamaCppAnswerGenerator`, `docker-compose.yml:llm` | Phi-4-mini weights and KV cache | llama.cpp, local GGUF | High |
| Retrieval | Domain service | Dense/sparse/hybrid search and heuristic/optional cross-encoder reranking | `app/retrieval/service.py`, `repository.py`, `reranker.py` | Reads SQL, sparse index, Qdrant | Embedder, Qdrant, SQLite | High |
| Qdrant | Vector store | Dense vector persistence and nearest-neighbor search | `app/ingestion/vector_writers.py`, `app/retrieval/service.py:QdrantDenseRetriever` | Vector collection and payload | Local disk/RAM | High |
| SQLite | Relational store/queue | Documents, chunks, sessions, ingestion jobs and leases | `app/db/models.py`, `session.py`, `ingestion/repository.py` | `citebot.db` plus WAL | Local filesystem | High |
| Sparse index | SQLite FTS5 index | Transactional lexical retrieval | `app/ingestion/sparse_index.py` | SQLite FTS5 metadata/text tables | Local filesystem | High |
| Observability/security | Cross-cutting | Trace IDs, request logs, in-memory metrics, API-key hooks, rate buckets | `app/observability/`, `app/core/security.py` | Process-local counters/deques | FastAPI | High |

## Primary request and async flows

### Research query
- Entrypoint: `POST /api/v1/research/query` or `/query/stream` (`app/api/routes/research.py`).
- Modules involved: research graph -> query embedding -> Qdrant/sparse retrieval -> fusion/reranking -> llama.cpp -> citation verification -> session persistence.
- State stores touched: Qdrant, SQLite FTS5, SQLite research sessions/chunks.
- External services called: local embedding and LLM HTTP services; public web search is disabled by the offline Compose default.
- Async work produced or consumed: request-scoped async work only; `LlamaCppAnswerGenerator` gates calls with an in-process semaphore configured to 1.
- Trust boundaries crossed: client input, API-key boundary, local model HTTP, stored session context.
- Blast radius: concurrent queries contend for the single LLM and embedding CPU budget; fallback retrieval can additionally load and re-embed every SQL chunk.
- Evidence: `app/agents/service.py`, `app/agents/generation.py:LlamaCppAnswerGenerator.generate`, `app/retrieval/service.py:RetrievalService.search`.
- Confidence: high.

### Queued document ingestion
- Entrypoint: admin job creation or CLI -> SQLite queue -> `citebot-worker`.
- Modules involved: `LocalCorpusLoader` -> normalizer -> chunker -> embedder -> object/structured stores -> SQLite -> Qdrant -> sparse index.
- State stores touched: source files, raw/structured document files, SQLite, Qdrant, SQLite FTS5.
- External services called: local embedding service and Qdrant; OCR runs in the worker process.
- Async work produced or consumed: one worker claims one job and processes documents serially, with leases/heartbeats/retries.
- Trust boundaries crossed: user-selected paths and document content, parser/OCR libraries, local service HTTP.
- Blast radius: bulk OCR competes with both model services for CPU/RAM; a worker crash can leave SQL/vector/sparse stores partially updated for the current document.
- Evidence: `app/ingestion/worker.py:run_worker`, `app/ingestion/service.py:_process_job`, `app/ingestion/repository.py:claim_next_job`.
- Confidence: high.

### Sparse and degraded dense retrieval
- Entrypoint: hybrid retrieval or Qdrant/pgvector fallback.
- Modules involved: `SparseIndex`, `RetrievalRepository`, `LocalDenseRetriever`.
- State stores touched: SQLite FTS5 result rows or bounded SQL candidates for explicit local fallback.
- External services called: local embedding service when dense fallback is used.
- Async work produced or consumed: serial batch search; no separate queue.
- Trust boundaries crossed: stored document text is sent to the local embedding service.
- Blast radius: FTS remains bounded by result count; explicit local fallback is capped and automatic fallback is disabled in the offline Compose profile.
- Evidence: `app/ingestion/sparse_index.py:_search_sync`, `SparseIndex.search`, `app/retrieval/service.py:LocalDenseRetriever.search`, `app/retrieval/repository.py:list_chunks`.
- Confidence: high.

## State stores and data ownership

| Store/model/table | Owner or module | Used by | Access pattern | Coupling/risk | Evidence |
| --- | --- | --- | --- | --- | --- |
| SQLite documents/chunks/sessions | DB/domain repositories | Ingestion, retrieval, agent | WAL-backed async transactions | Suitable for one worker/small query load; long writes and multi-process contention still need measurement | `app/db/session.py:initialize`, `app/db/models.py` |
| SQLite ingestion jobs | Ingestion repository | API and worker | Atomic claim, lease, heartbeat, retry | Strong single-host queue foundation; no global scheduler/admission policy | `app/ingestion/repository.py`, `app/ingestion/worker.py` |
| SQLite FTS5 sparse index | `SparseIndex` | Worker and API | Indexed MATCH query; transactional document replacement | Requires SQLite FTS5 support; sibling legacy JSON is retained for migration | `app/ingestion/sparse_index.py` |
| Qdrant collection | Vector adapters | Worker and retrieval | HTTP upsert/search | 1,024 float32 values are about 4 KiB/chunk before HNSW/payload overhead; resident usage grows materially with corpus | `app/core/config.py:embedding_dimension`, `app/ingestion/vector_writers.py:QdrantWriter` |
| Raw/structured documents | Object store | Ingestion and provenance | Atomic structured writes/local filesystem | Disk capacity and backup are operational dependencies | `app/ingestion/object_store.py` |
| Model artifacts | Provisioning/runtime | Embedding, LLM, OCR | Read-only bind mount verified by manifest | Actual sizes are absent now, so RAM/disk claims require post-provision measurement | `models/README.md`, `models/manifest.lock.example.json`, `app/core/model_manifest.py` |

## External dependencies

| Dependency | Purpose | Call sites/config | Sync or async | Failure impact | Boundary/adapter | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| llama.cpp server | Local answer generation | `LLM_BASE_URL`, Compose `llm` | Async HTTP | Research generation fails or queues behind semaphore | `LlamaCppAnswerGenerator` | `app/agents/generation.py` |
| TEI embedding server | Local embeddings | `EMBEDDING_BASE_URL`, Compose `embedding` | Async HTTP, batches | Ingestion and dense/hybrid query fail | `LocalHttpEmbedder` | `app/ingestion/embedder.py` |
| Qdrant | Dense storage/search | `QDRANT_URL`, `ENABLE_QDRANT` | Async HTTP | Compose fails degraded rather than triggering full-corpus local re-embedding; explicit local mode is capped | Writer/retriever adapters | `app/retrieval/service.py:_backend_order` |
| PaddleOCR/Tesseract | OCR | `OCR_*` settings | In worker process | Job slows/fails; CPU/RAM spike | OCR engine classes | `app/ingestion/ocr.py`, `loaders.py:_ocr_blocks` |
| PostgreSQL/pgvector | Optional alternate persistence/vector path | Compose `postgres` profile | Async SQL | No default-stack impact | SQLAlchemy/pgvector adapters | `docker-compose.yml:postgres`, `app/ingestion/vector_writers.py` |
| OpenAI/Gemini/Tavily | Development-mode optional providers | Provider factories | Async HTTP | Disabled in `RUNTIME_MODE=offline` | Dedicated adapters | `app/core/config.py`, `app/agents/generation.py`, `app/tools/web_search.py` |

## Trust boundaries
- Inbound clients and public APIs: the API is loopback-published by Compose, but research/admin API keys default to unset. Treat this as single-user only unless keys and a trusted proxy are configured (`docker-compose.yml`, `app/core/config.py`).
- Auth/session boundaries: API-key scopes exist; sessions have no user/tenant owner. Do not expose the system to multiple mutually untrusted users (`app/core/security.py`, `app/db/models.py`).
- Admin/internal boundaries: ingestion and evaluation routes can trigger high-cost work; the 16 GB Compose profile defaults to 10 admin requests per minute.
- Tenant/data isolation boundaries: no tenant isolation is present.
- Webhook or third-party callback boundaries: none observed.
- File/object storage boundaries: loader size is capped at 50 MB per source file and PDFs at 500 pages, but decompressed JSON, pixel buffers, recursive directory totals, and document counts are not bounded (`app/core/config.py`, `app/ingestion/loaders.py:load`).
- Queue/cache boundaries: SQLite is the durable queue. Metrics and rate limits are API-process local. The sparse index is shared by API and worker without an inter-process coordination mechanism.

## Architectural strengths
- Expensive ingestion is already separated from the API by a durable leased SQLite queue and a single sequential worker (`app/ingestion/worker.py`, `app/ingestion/repository.py`).
- Generation concurrency is explicitly bounded with a semaphore, and embedding calls are batched (`app/agents/generation.py:LlamaCppAnswerGenerator`, `app/ingestion/embedder.py:LocalHttpEmbedder`).
- SQLite WAL and busy timeout reduce basic API/worker contention (`app/db/session.py:initialize`).
- Model artifacts are offline, manifest-verified, and mounted read-only; internal services are not host-published (`models/README.md`, `docker-compose.yml`).
- Health/readiness checks cover SQLite, Qdrant, embedding, and LLM dependencies (`app/core/health.py:HealthService.readiness`).
- Provider adapters and the central service container make resource-oriented changes localized rather than rewrite-scale (`app/core/lifecycle.py:build_container`).
- Targeted capacity/config/retrieval checks pass: 17 tests passed in the clean non-DB subset and `python -m ruff check app tests` passes. The full DB-backed suite remains blocked in this sandbox by a standalone `aiosqlite` connection stall and must be rerun in the normal runtime.

## Architectural risks

### CAP-01: Resource caps need real-host calibration
- Severity: high
- Category: deployment
- Evidence: Compose now defines 1 GB API, 2 GB worker, 2.5 GB embedding, 5 GB LLM, and 2 GB Qdrant limits with restart policies; the exact model RSS has not been measured (`docker-compose.yml`).
- Why it matters: the kernel, Docker, LLM weights/KV cache, embedding model, OCR, Qdrant, API, worker, and filesystem cache can exceed 16 GB when ingestion and queries overlap.
- Likely blast radius: host swapping/OOM, all services, SQLite job progress, and user latency.
- Recommended action: validate the aggregate 12.5 GB starting ceiling with provisioned models, leave 2-3 GB available to the host, and tune only from RSS/PSS, swap, latency, and restart measurements. Add host OOM alerts.
- Confidence: high that limits are configured; medium for capacity until models are provisioned.

### CAP-02: Default LLM context is aggressive for 16 GB CPU-only operation
- Severity: high
- Category: operations
- Evidence: settings, `.env.example`, and Compose default `LLM_CONTEXT_TOKENS=4096`; generation concurrency and research admission are 1 active request with two waiting slots (`app/core/config.py`, `docker-compose.yml`, `app/core/admission.py`).
- Why it matters: KV cache grows with context and can consume substantial memory in addition to model weights; longer prompts also increase CPU latency.
- Likely blast radius: LLM RSS, first-token latency, request timeout, and host headroom.
- Recommended action: keep 4,096 as the default, expose 8,192 only as an opt-in profile that passes a soak test, and benchmark the exact i7 with explicit model thread environment variables.
- Confidence: high.

### CAP-03: Large single-file parsing still needs a ceiling
- Severity: high
- Category: operations
- Evidence: worker processing now consumes `LocalCorpusLoader.iter_load`, JSONL is line-streamed, and per-file/total byte, document, and PDF-page budgets are enforced; a single JSON array is still parsed as one bounded file (`app/ingestion/loaders.py`, `app/ingestion/service.py:_process_job`).
- Why it matters: a directory of individually valid files can exceed memory even though each file is below 50 MB.
- Likely blast radius: worker OOM, job retries, API/LLM eviction, and partial indexing.
- Recommended action: keep the 25 MB/file, 100-page, 500-document, and 512 MB total defaults; add an incremental parser or explicit JSON-array size warning if larger JSON files become common.
- Confidence: high.

### CAP-04: Sparse index migration and rebuildability need operational coverage
- Severity: high
- Category: data ownership
- Evidence: sparse retrieval now uses SQLite FTS5 MATCH queries and transactional document replacement; legacy JSON is read only for one-time migration (`app/ingestion/sparse_index.py`).
- Why it matters: this will become the dominant latency, RAM, and write-amplification path well before Qdrant reaches its useful capacity.
- Likely blast radius: hybrid query latency, worker/API races, index corruption after interrupted writes, and disk wear.
- Recommended action: add a rebuild/reconciliation command and test migration/recovery after process termination; retain the legacy JSON until the SQLite file is verified.
- Confidence: high.

### CAP-05: Dense fallback turns a dependency failure into a load spike
- Severity: high
- Category: dependency
- Evidence: `LocalDenseRetriever.search` is limited to `MAX_LOCAL_DENSE_CANDIDATES`; `ALLOW_LOCAL_DENSE_FALLBACK=false` in Compose removes it from automatic backend order (`app/retrieval/service.py`, `app/core/config.py`, `docker-compose.yml`).
- Why it matters: when Qdrant is unhealthy, the API can simultaneously increase SQLite, embedding, CPU, network, and memory load.
- Likely blast radius: embedding service saturation and system-wide latency/OOM.
- Recommended action: keep automatic fallback disabled in offline deployments, return degraded retrieval when Qdrant is unavailable, and use explicit local mode only for small test corpora below the hard candidate cap.
- Confidence: high.

### CAP-06: Qdrant capacity still needs a corpus benchmark
- Severity: medium
- Category: data ownership
- Evidence: embeddings are 1,024-dimensional and Qdrant now has a 2 GB Compose limit, but collection growth/on-disk vector behavior has not been benchmarked (`app/core/config.py`, `docker-compose.yml:qdrant`).
- Why it matters: raw float32 vectors alone use about 195 MiB/50k, 391 MiB/100k, 977 MiB/250k, 1.91 GiB/500k, and 3.81 GiB/1M chunks, before HNSW, payload, allocator, and cache overhead.
- Likely blast radius: Qdrant RSS, disk, startup time, retrieval latency, and host headroom.
- Recommended action: use 250k chunks as the initial 16 GB ceiling, alert at 70/85% of the measured Qdrant budget, and test on-disk vectors/payload or quantization before exceeding it. Do not run the optional PostgreSQL/pgvector profile concurrently on this host unless measured.
- Confidence: high for raw-vector arithmetic; medium for total Qdrant usage.

### CAP-07: Host-level capacity metrics are still required
- Severity: medium
- Category: operations
- Evidence: research/admin limits default to 30/10 requests per minute, research admission is bounded to one active plus two waiting, and process peak RSS is exposed; host/container/model metrics remain outside the app (`app/core/config.py`, `app/core/admission.py`, `app/observability/metrics.py`).
- Why it matters: a burst can build an unbounded LLM wait queue, while operators cannot see resource exhaustion early.
- Likely blast radius: query timeouts, memory retained by waiting requests, delayed ingestion, and poor diagnosis.
- Recommended action: retain the bounded app controls, add host/container/model/queue/stage metrics with retention outside process memory, and alert on queue age, RSS, swap, and OOM/restart events.
- Confidence: high.

## Operational maturity observations
- Health/readiness: dependency checks exist and use two-second local-service timeouts. Add a shallow readiness probe and separate deep diagnostic endpoint so frequent probes do not contend with model inference.
- Graceful shutdown: API disposes the DB engine; worker cancellation closes its container. The current job lease supports recovery, but per-document multi-store writes are not atomic.
- Config and secrets: central validation and offline URL allowlisting are strengths. Add a named `hardware-16gb` Compose/env profile so safe limits are reproducible.
- Logging: request/trace logging exists. Add job stage duration, model queue wait, corpus counts, Qdrant usage, peak RSS, OOM/restart reason, and disk-free logs without document content.
- Metrics/tracing: in-memory metrics now include peak process RSS and request/rate-limit counters. Host/container RSS/CPU, swap, Qdrant vector count, queue age, model tokens/second, and p50/p95 latency still require host-side collection.
- Timeouts/retries: model and local HTTP calls have timeouts; ingestion jobs retry. Add retry backoff/jitter and distinguish retryable dependency failure from deterministic parser/model errors.
- Rate limiting/backpressure: research requests have bounded admission (one active, two waiting), rate limits default to 30 research/10 admin requests per minute, and the worker remains sequential. CPU thread tuning and ingestion-vs-generation scheduling still require measurement.
- Idempotency: content hashes and leased jobs help. SQL, Qdrant, and sparse updates can still diverge; record stages and make writes replay-safe before automatic retries.
- Deploy/rollback signals: tests, manifest validation, Compose static parsing, and resource limits exist. Docker startup/model benchmark and vector rollback remain host-only gates.

Suggested initial 16 GB operating profile (measurement targets, not proven guarantees):

| Control | Initial value | Scale-up gate |
| --- | --- | --- |
| LLM context | 4,096 tokens | 8,192 only if peak available RAM stays above 2 GB and p95 meets target |
| Concurrent generations | 1 active, at most 2 waiting | Increase only after a 30-minute mixed-load soak |
| Ingestion workers/jobs | 1 | Keep at 1 on CPU-only 16 GB host |
| OCR pages in flight | 1 | Keep at 1; loader currently processes sequentially |
| Embedding batch | 4 initially; test 8 | Choose best p95/RSS result |
| Model CPU threads | Start 4-6 per active model; avoid simultaneous full-core pools | Tune to physical cores and thermal behavior |
| PDF/file limits | 100 pages, 25 MB default | Raise only for an isolated ingestion window |
| Qdrant corpus | <=250k chunks initially | Larger only with measured RSS and on-disk/quantization evaluation |
| Swap | Small emergency swap allowed, zero sustained activity | Any sustained swap is a failed capacity test |
| Free headroom | >=2 GB minimum; >=3 GB preferred at peak | Required before release |

## Recommended refactoring priorities
1. Provision and verify the pinned model artifacts, then run the 16 GB Compose profile with 4,096-token context and capture peak RSS, swap, CPU, and thermal behavior.
2. Add a rebuild/reconciliation command for SQLite FTS5 and test process termination between SQLite, Qdrant, and object-store writes.
3. Add host/container/model metrics and a 30-minute benchmark gate: cold start, one query, two-query burst, ingestion-only, and mixed ingestion/query load.
4. Reassess the 250k chunk ceiling with the real corpus; evaluate Qdrant on-disk vectors/quantization only after relevance and latency comparison.

## Unknowns and confidence
- Unknown: exact Core i7 generation, physical-core count, AVX/AVX2/AVX-512 support, thermal envelope, and storage type. Why it matters: llama.cpp/TEI throughput can vary by multiples. How to verify safely: record `lscpu`, disk type, and sustained temperature/clock behavior on the target host, then benchmark the pinned images.
- Unknown: provisioned model artifact sizes and actual process RSS. Why it matters: the repository currently contains only manifest examples. How to verify safely: provision and verify models, then capture idle, single-query, full-context, OCR, and mixed-load RSS/PSS.
- Unknown: normal document mix, scan rate, languages, average chunks/document, and total corpus target. Why it matters: OCR cost and Qdrant/sparse capacity depend on these values. How to verify safely: build a redacted representative corpus and report chunk/OCR distributions before setting final limits.
- Unknown: acceptable latency and concurrent-user SLO. Why it matters: “smoothly” could mean one patient local user or several interactive clients. How to verify safely: define first-token/full-answer/retrieval p95 and maximum queue wait; the recommendations assume one active user and occasional second requests.
- Unknown: real Compose startup and runtime behavior. Why it matters: Docker is unavailable in this analysis environment, and model artifacts are not provisioned. How to verify safely: run `docker compose config`, manifest verification, cold-start, health, benchmark, and 30-minute mixed-load soak on the actual host.
- Unknown: whether LAN/multi-user exposure is required. Why it matters: current sessions are not tenant-owned and API keys default to unset. How to verify safely: keep loopback-only until an explicit auth/isolation review is completed.
