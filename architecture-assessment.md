# Backend Architecture Assessment

## Executive summary
CiteBot is a FastAPI modular monolith with clearly separated ingestion, retrieval, research-agent, evaluation, persistence, and observability modules. It already defaults to SQLite, filesystem storage, deterministic in-process embeddings, and deterministic answer generation, but those `local` providers are test/development substitutes rather than the real local models proposed in the rough draft. The local-only pivot can therefore reuse the existing adapter and service-container seams, but it must replace the meaning of `local`, add structured document/OCR processing, make Qdrant the default dense index, and remove deployed-runtime paths to hosted model and web-search APIs.

The largest migration risks are index incompatibility when moving from 32-dimensional deterministic vectors to Qwen3 embeddings, loss of citation precision because the current document model cannot represent element coordinates and OCR confidence, and resource contention because ingestion runs inside the API process without a durable worker queue. The safest path is an incremental adapter migration with a versioned reindex, not a rewrite. Confidence is high for the application shape and current boundaries, and medium for production behavior because no deployment inventory outside this repository was inspected.

## System overview
- Primary language/framework: Python 3.12+, FastAPI, Pydantic Settings, SQLAlchemy async, and LangGraph (`pyproject.toml:5-29`, `app/main.py:16`, `app/agents/service.py`).
- Topology: modular monolith in one API process, with separately deployed PostgreSQL, Qdrant, Redis, nginx, and optional Dozzle containers in the current Compose stack (`docker-compose.yml:1-83`).
- Request lifecycle: FastAPI route -> dependency-injected `ServiceContainer` -> ingestion, retrieval, research-agent, or evaluation service -> SQL/filesystem/vector backends and optional external HTTP adapters (`app/core/lifecycle.py:34-140`, `app/api/routes/`).
- Background processing model: no separate application worker is present; ingestion jobs are recorded but executed by `IngestionService` in the API runtime (`app/ingestion/service.py:20-162`, `app/db/models.py:70-91`).
- Deployment model: a single API image under Docker Compose, fronted by nginx, with host-published ports for the API and data services (`Dockerfile:30`, `docker-compose.yml:1-83`). A direct SQLite/filesystem development mode is also documented (`README.md:84-90`).
- Confidence: high for repository topology; medium for deployed topology outside Compose.

## Component inventory

| Component | Type | Responsibility | Key files | Owned state | Dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| FastAPI application | API/runtime | Routes, lifecycle, dependency injection, middleware | `app/main.py`, `app/core/lifecycle.py`, `app/api/routes/` | Process-local service container | All application services | High |
| Ingestion | Domain module | Load, normalize, chunk, embed, persist, and index documents | `app/ingestion/service.py`, `loaders.py`, `chunker.py`, `embedder.py` | Ingestion jobs, documents, chunks, raw text, sparse index, vectors | SQL, filesystem, pgvector, Qdrant, model APIs | High |
| Retrieval | Domain module | Dense/sparse retrieval, backend routing, fusion, reranking, explanation | `app/retrieval/service.py`, `repository.py`, `reranker.py` | Reads chunks and indexes | SQL, Qdrant, embedder, sparse index | High |
| Research agent | Domain module | Query planning, retrieval, optional tools, answer generation, citation checks, session persistence | `app/agents/service.py`, `generation.py`, `session_store.py`, `prompts.py` | Research sessions and trace/citation state | Retrieval, SQL, Tavily, Python sandbox, answer provider | High |
| Evaluation | Domain module/admin API | Dataset runs, quality thresholds, optional RAGAS evaluation | `app/evaluation/`, `app/api/routes/admin_evaluation.py` | Evaluation artifacts and run state | Research service, optional hosted evaluator | High |
| SQL persistence | Infrastructure adapter | Durable application records and session state | `app/db/models.py`, `session.py`, `base.py` | Documents, chunks, jobs, research sessions | SQLite or PostgreSQL | High |
| Object storage | Infrastructure adapter | Persist canonical/raw document text | `app/ingestion/object_store.py` | Files under `OBJECT_STORAGE_PATH` | Local filesystem | High |
| Vector storage | Infrastructure adapters | Store/query dense vectors | `app/ingestion/vector_writers.py`, `app/retrieval/service.py` | pgvector table and/or Qdrant collection | PostgreSQL, Qdrant HTTP API | High |
| Sparse index | Infrastructure adapter | Local lexical retrieval | `app/ingestion/sparse_index.py` | JSON index file | Local filesystem | High |
| Observability/security middleware | Cross-cutting | Metrics, trace IDs, API-key and rate-limit controls | `app/observability/`, `app/core/security.py` | In-memory metrics/rate buckets | FastAPI process | Medium |
| Compose infrastructure | Deployment | API proxying, relational/vector stores, unused/incipient cache, logs | `docker-compose.yml`, `nginx/nginx.conf` | Named volumes and container logs | Docker | High |

## Primary request and async flows

### Document ingestion
- Entrypoint: admin ingestion route or `citebot-ingest` CLI (`app/api/routes/admin_ingestion.py:25`, `pyproject.toml:26-27`).
- Modules involved: loader -> normalizer -> chunker -> embedder -> repository/object store/sparse index/vector writers (`app/ingestion/service.py:20-162`).
- State stores touched: SQL document/chunk/job records, local raw-text storage, JSON sparse index, and optionally pgvector/Qdrant.
- External services called: OpenAI or Gemini embeddings when selected; Qdrant over HTTP when enabled (`app/ingestion/embedder.py:58-143`, `app/ingestion/vector_writers.py:113-184`).
- Async work produced or consumed: job records exist, but no independent queue/worker boundary was found.
- Trust boundaries crossed: admin API/CLI input, local file paths and file contents, optional hosted embedding APIs, and vector-store HTTP.
- Blast radius: changing the canonical representation or embedding dimension affects loaders, schemas, SQL rows, object storage, sparse indexing, both vector writers, retrieval, citations, evaluation fixtures, and all previously indexed data.
- Evidence: `app/ingestion/service.py:20-162`, `app/db/models.py:19-91`, `app/ingestion/loaders.py:11-54`.
- Confidence: high.

### Research query and answer generation
- Entrypoint: `POST /api/v1/research/query` and streaming variant (`app/api/routes/research.py:26-39`).
- Modules involved: research graph, session store, retrieval service, optional web/Python tools, answer generator, citation verifier (`app/agents/service.py`, `app/tools/`, `app/agents/generation.py`).
- State stores touched: SQL-backed research session state and retrieval indexes.
- External services called: OpenAI or Gemini for answers and Tavily for optional web enrichment when configured (`app/agents/generation.py:37-133`, `app/tools/web_search.py:28-147`).
- Async work produced or consumed: request-scoped async graph operations; no external queue.
- Trust boundaries crossed: public/local client input, persisted conversation state, optional hosted APIs, and local Python subprocess execution when enabled.
- Blast radius: local-only enforcement affects query classification, web-tool construction, answer-generation adapters, configuration validation, evaluation, streaming error handling, and documentation.
- Evidence: `docs/architecture/system-design.md:32-40`, `app/agents/service.py:89-617`, `app/core/config.py:70-145`.
- Confidence: high.

### Hybrid retrieval
- Entrypoint: research graph or admin search route (`app/api/routes/admin_ingestion.py:55`, `app/retrieval/service.py:270`).
- Modules involved: embedder, local/pgvector/Qdrant dense backends, sparse index, fusion, reranker.
- State stores touched: SQL chunks, pgvector table, Qdrant collection, sparse-index file.
- External services called: Qdrant over local HTTP; a hosted embedding API can currently be called for query embeddings.
- Async work produced or consumed: none beyond request-scoped async I/O.
- Trust boundaries crossed: query/filter input and vector-store HTTP boundary.
- Blast radius: model or dimension changes require re-embedding and a versioned vector collection; backend-default changes affect readiness and retrieval explanations.
- Evidence: `app/retrieval/service.py:25-401`, `app/retrieval/reranker.py:13-156`, `app/core/config.py:36-69`.
- Confidence: high.

## State stores and data ownership

| Store/model/table | Owner or module | Used by | Access pattern | Coupling/risk | Evidence |
| --- | --- | --- | --- | --- | --- |
| `documents` / `chunks` | Ingestion/DB | Ingestion, retrieval, citations | SQL writes during ingestion; reads during retrieval | Current chunk fields preserve page/section/offsets but not structured elements, bounding boxes, extraction method, or OCR confidence | `app/db/models.py:19-67` |
| `ingestion_jobs` | Ingestion | Admin API/CLI | Create/update/read job summaries | Records status but does not form a durable worker queue | `app/db/models.py:70-91`, `app/ingestion/service.py:56-152` |
| `research_sessions` | Research agent | Query/replay/evaluation | Upsert and replay JSON state | User/tenant ownership is not represented in the model | `app/db/models.py:94-104`, `app/agents/session_store.py:11-49` |
| Raw/normalized document files | Ingestion object store | Ingestion/citation support | Local filesystem writes by document ID | Local permissions, backup, deletion, and encryption policy are operational dependencies | `app/ingestion/object_store.py:6-24` |
| Sparse JSON index | Ingestion/retrieval | Hybrid retrieval | Process-local file update/read | Single-file concurrency and crash consistency need validation before a worker split | `app/core/config.py:32-35`, `app/ingestion/sparse_index.py` |
| pgvector embeddings | Ingestion/retrieval | Dense retrieval | SQL upsert and broad read for application-side scoring | Optional duplicate vector ownership alongside Qdrant; dimension is schema-coupled | `app/ingestion/vector_writers.py:14-111`, `app/retrieval/service.py:73-159` |
| Qdrant collection | Ingestion/retrieval | Dense retrieval | HTTP collection creation, upsert, search | Collection dimension and embedding version require coordinated migration | `app/ingestion/vector_writers.py:113-184`, `app/retrieval/service.py:161-239` |

## External dependencies

| Dependency | Purpose | Call sites/config | Sync or async | Failure impact | Boundary/adapter | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| OpenAI API | Embeddings, answers, evaluation | `OPENAI_API_KEY`, embedder/generator/evaluator | Async HTTP | Ingestion/query/evaluation failure when selected | Dedicated classes, but hard-coded public URLs | `app/ingestion/embedder.py:58-80`, `app/agents/generation.py:37-71` |
| Gemini API | Embeddings, answers, evaluation | `GEMINI_API_KEY`, provider settings | Async HTTP | Same as OpenAI | Dedicated classes | `app/ingestion/embedder.py:82-120`, `app/agents/generation.py:73-113` |
| Tavily | Optional web search | `TAVILY_*` settings | Async HTTP with retry | Optional enrichment degrades/fails | `BaseWebSearchTool` adapter | `app/tools/web_search.py:15-147`, `app/core/config.py:120-134` |
| Qdrant | Dense vector storage/search | `QDRANT_URL`, `ENABLE_QDRANT` | Async HTTP | Dense retrieval/index writes fail; current routing can fall back locally | Writer/retriever adapters | `app/ingestion/vector_writers.py:113-184`, `app/retrieval/service.py:161-239` |
| PostgreSQL/pgvector | SQL persistence and optional vector index | `DATABASE_URL`, `ENABLE_PGVECTOR` | Async SQL | Readiness and durable state fail; SQLite can be used in local mode | SQLAlchemy/session and writer/retriever adapters | `app/db/session.py`, `app/ingestion/vector_writers.py:14-111` |
| Redis | Compose service | Compose only | Unknown | No application failure found from repository evidence | No application adapter found | `docker-compose.yml:30`, `docker-compose.yml:76-80` |
| Corpus source APIs | Offline corpus acquisition scripts | `scripts/download_corpus.py`, `scripts/arxiv_pdf_parser.py` | Synchronous HTTP | Dataset acquisition fails; deployed RAG runtime need not depend on them | Script boundary only | `scripts/download_corpus.py`, `scripts/arxiv_pdf_parser.py:32-42` |

## Trust boundaries
- Inbound clients and public APIs: health, research, ingestion-admin, and evaluation-admin routes. Compose currently binds API/nginx and backing-service ports to the host (`app/api/routes/`, `docker-compose.yml:23-80`).
- Auth/session boundaries: optional research/admin API keys and in-memory request-rate buckets exist, but research sessions do not contain a user or tenant owner (`app/core/config.py:102-115`, `app/db/models.py:94-104`).
- Admin/internal boundaries: ingestion and evaluation are separate admin routers, but local-only deployment still needs loopback binding and explicit admin-key behavior if LAN exposure is enabled.
- Tenant/data isolation boundaries: no tenant model or per-tenant index/storage namespace was observed. The present design should be treated as single-user/single-trust-domain.
- Webhook or third-party callback boundaries: none observed.
- File/object storage boundaries: arbitrary local paths supplied to admin ingestion/CLI cross into parser and storage code; upload-size, MIME, page-count, and decompression limits are not represented in current evidence.
- Queue/cache boundaries: no application queue was found. Rate limiting and metrics are in process; the Redis container does not appear integrated.

## Architectural strengths
- Provider construction is centralized behind embedder, reranker, generator, vector-writer, and web-tool interfaces, which gives the pivot natural adapter seams (`app/core/lifecycle.py:58-130`).
- SQL models already preserve source URI, content hash, page, section, character offsets, model/version metadata, and location markers needed as a base for traceable citations (`app/db/models.py:19-67`).
- Retrieval already supports Qdrant, local fallback, hybrid fusion, reranking, filters, and backend explanation (`app/retrieval/service.py:239-495`).
- Configuration is centralized and validated at startup (`app/core/config.py:10-371`).
- Health/readiness, trace IDs, metrics, API-key hooks, and bounded external HTTP timeouts already exist (`app/api/routes/health.py`, `app/observability/`, `app/core/security.py`).

## Architectural risks

### LOC-01: `local` currently means deterministic test doubles
- Severity: blocker
- Category: dependency
- Evidence: `LocalEmbedder` generates repeatable pseudo-vectors and `LocalAnswerGenerator` emits deterministic answers (`app/ingestion/embedder.py:24-56`, `app/agents/generation.py:28-35`).
- Why it matters: retaining these as the production defaults would claim local RAG capability without real semantic embedding or grounded model generation.
- Likely blast radius: configuration, provider factories, ingestion/reindexing, retrieval quality, research answers, tests, evaluation, Compose, and docs.
- Recommended action: rename deterministic providers to `test`, add explicit local HTTP/model-runtime adapters, and reject test providers outside test/development.
- Confidence: high.

### LOC-02: Deployed runtime can still call public APIs
- Severity: blocker
- Category: trust boundary
- Evidence: hosted provider choices and Tavily are accepted in settings and use hard-coded public endpoints (`app/core/config.py:36-46`, `70-75`, `120-134`, `223-247`; `app/agents/generation.py:37-113`; `app/tools/web_search.py:28-147`).
- Why it matters: API-key absence is not equivalent to a fail-closed offline guarantee, and an environment change can silently move private content outside the machine.
- Likely blast radius: configuration, provider factories, research planning, evaluation, container networking, CI, and release documentation.
- Recommended action: introduce a fail-closed runtime network policy, remove hosted providers from the deployed image/config contract, separate networked corpus bootstrap tooling, and run application services on an internal Docker network.
- Confidence: high.

### LOC-03: Current schema cannot preserve serious OCR structure
- Severity: high
- Category: data ownership
- Evidence: chunks store flat text, offsets, optional section/page/location, but no page element type, bounding box, extraction method, OCR confidence, or table representation (`app/db/models.py:45-67`). Loaders currently focus on text/JSON corpora (`app/ingestion/loaders.py:11-54`).
- Why it matters: flattening complex pages weakens citations, table retrieval, quality diagnostics, and selective fallback decisions.
- Likely blast radius: canonical schemas, migrations, object storage, chunker, vector payloads, retrieval results, citation verifier, API contracts, and evaluation fixtures.
- Recommended action: add a versioned structured-document model and structure-aware chunk provenance before integrating OCR.
- Confidence: high.

### LOC-04: Embedding cutover requires a coordinated reindex
- Severity: high
- Category: data ownership
- Evidence: the current default dimension is 32 and vector dimensions are embedded in pgvector/Qdrant initialization (`app/core/config.py:40`, `app/ingestion/vector_writers.py:29-45`, `136-151`).
- Why it matters: Qwen3 embeddings cannot be written into or queried against the existing collection safely; partial cutover can return invalid or incomparable results.
- Likely blast radius: every embedding row/point, collection naming, readiness, filters, evaluation baselines, and rollback.
- Recommended action: create a new versioned collection/index, dual-read only for validation, atomically switch the active alias/config, and retain the prior index until acceptance gates pass.
- Confidence: high.

### LOC-05: CPU/RAM-heavy work has no isolation or backpressure
- Severity: high
- Category: operations
- Evidence: no worker service or queue consumer was found; ingestion is owned by the API service while Compose includes Redis without an application client (`app/ingestion/service.py:20-162`, `docker-compose.yml:76-80`).
- Why it matters: OCR, embedding, and model generation can overlap and exhaust a 16 GB CPU-only host, taking down the API or corrupting in-flight jobs.
- Likely blast radius: API latency/readiness, ingestion state, local model services, Qdrant, and host stability.
- Recommended action: use a durable SQLite-backed job queue and a separate document worker with OCR concurrency 1, bounded embedding batches, and an API-side generation semaphore of 1 by default.
- Confidence: high.

### LOC-06: Default Compose exposure is broader than a private laptop needs
- Severity: medium
- Category: deployment
- Evidence: API and backing services define host port mappings (`docker-compose.yml:23-80`), while the target draft only requires the API on loopback.
- Why it matters: Qdrant, Redis, PostgreSQL, log viewers, or the API may be reachable from unintended interfaces depending on Docker/host configuration.
- Likely blast radius: all stored documents, vectors, sessions, and logs.
- Recommended action: publish only `127.0.0.1:${CITEBOT_PORT}:8000` by default, remove host ports from internal services, and put all service-to-service traffic on an `internal: true` network.
- Confidence: high.

## Operational maturity observations
- Health/readiness: health, readiness, version, and metrics routes exist; readiness checks SQL and optionally Qdrant (`app/api/routes/health.py`, `app/core/health.py`). Local model and OCR service readiness must be added.
- Graceful shutdown: the FastAPI lifespan initializes and closes the service container (`app/core/lifecycle.py:46-55`, `132-140`). Worker lease recovery and subprocess/model shutdown are not yet applicable.
- Config and secrets: environment-based Pydantic settings validate several provider combinations. Local-only URL/host allowlisting and model-artifact validation are absent.
- Logging: Python logging and trace IDs are present, but document content/redaction policy is not explicit.
- Metrics/tracing: in-memory metrics exist; no distributed trace backend is required for the proposed single-host system, but cross-service request/job IDs should be propagated.
- Timeouts/retries: external HTTP calls use bounded timeouts and Tavily retries. Local model/OCR adapters need their own queue, timeout, cancellation, and overload behavior.
- Rate limiting/backpressure: request rate limiting is process-local; no OCR/embed/generation resource semaphore or durable queue exists.
- Idempotency: document content hashes, force-reindex flags, and version fields provide a base; job leasing/retry idempotency and partial vector-write recovery need explicit design.
- Deploy/rollback signals: CI, release docs, health checks, and versioned embedding/index fields exist. There is no model manifest/hash gate or documented vector-index rollback procedure.

## Recommended refactoring priorities
1. Establish and test a fail-closed local-only configuration/network contract; distinguish real local providers from deterministic test doubles.
2. Add local embedding and llama.cpp-compatible generation adapters behind the current interfaces, with readiness and bounded concurrency.
3. Introduce a versioned structured-document schema and selective native-text/OCR pipeline before changing chunking.
4. Split ingestion into a durable SQLite-backed worker flow and enforce single-host resource budgets.
5. Build a new Qwen3-backed Qdrant index version and perform an evaluation-gated cutover.
6. Harden Compose for loopback-only access, internal networking, pinned offline model artifacts, and no runtime downloads.
7. Remove or isolate hosted-provider, Tavily, Redis, pgvector, nginx, and networked corpus tooling that are outside the default local runtime.

## Unknowns and confidence
- Unknown: whether the target is strictly single-user or may later be exposed to a LAN. Why it matters: session ownership, auth, TLS, and tenant isolation requirements change substantially. How to verify safely: confirm deployment scope before enabling any non-loopback bind; the plan defaults to single-user loopback-only.
- Unknown: exact 16 GB host CPU instruction set, OS, and acceptable query latency. Why it matters: llama.cpp image/flags, quantization, context size, and OCR throughput depend on them. How to verify safely: add a preflight command and benchmark gate before freezing runtime defaults.
- Unknown: licensing/redistribution policy for bundled model artifacts. Why it matters: prebuilt offline images may redistribute large third-party weights. How to verify safely: record model licenses and hashes in a reviewed manifest before release packaging.
- Unknown: required languages and prevalence of tables/scans. Why it matters: OCR language packs, confidence thresholds, and evaluation corpus selection depend on actual documents. How to verify safely: build a redacted representative document fixture set and measure native extraction coverage and OCR fallback rate.
- Unknown: whether existing indexed data must be preserved. Why it matters: the embedding and canonical-schema changes require a full reindex. How to verify safely: inventory current volumes and retain an export plus the old collection until the cutover is accepted.

