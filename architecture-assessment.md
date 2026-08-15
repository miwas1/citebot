# Backend Architecture Assessment

## Executive summary
CiteBot is a local-first Python modular monolith split into API and document-worker
processes. PostgreSQL/pgvector is now the single production data service: it owns
relational state, queue leases, workflow checkpoints, full-text retrieval, and
dense vectors. Filesystem storage remains intentionally separate for uploaded and
structured document artifacts, while local embedding and llama.cpp services own
model inference. Removing SQLite and Qdrant reduces operational coupling and
eliminates cross-database retrieval consistency, but the cutover still needs a
live PostgreSQL test and an explicit decision about retaining legacy history.

## System overview
- Primary language/framework: Python, FastAPI, Pydantic, async SQLAlchemy, LangGraph, PyMuPDF/PaddleOCR.
- Topology: modular monolith with separate API and ingestion-worker processes plus PostgreSQL and local model services.
- Request lifecycle: HTTP middleware/auth -> route -> service container -> retrieval/research service -> PostgreSQL and local model HTTP -> persisted response/session.
- Background processing model: PostgreSQL lease-based job queue polled by a document worker; ingestion is sequential within the shipped worker profile.
- Deployment model: Docker Compose; only Caddy/API and operator UI endpoints are host-visible, while PostgreSQL and model services use the internal network.
- Confidence: high for static architecture; medium for operational readiness because Docker/PostgreSQL integration could not run in this workspace.

## Component inventory

| Component | Type | Responsibility | Key files | Owned state | Dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| API | Runtime process | Research, projects, ingestion administration, health and metrics | `app/main.py`, `app/api/routes/`, `app/core/lifecycle.py` | Process-local metrics/rate state | PostgreSQL, embedding, LLM | high |
| Document worker | Runtime process | Claim durable jobs and execute ingestion | `app/ingestion/worker.py`, `app/ingestion/service.py` | Job lease/progress in PostgreSQL | PostgreSQL, parsers/OCR, embedding | high |
| PostgreSQL/pgvector | Data service | Relational state, queue, checkpoints, FTS and ANN vectors | `app/db/`, `app/retrieval/service.py`, `app/ingestion/vector_writers.py` | Durable application/query state | Persistent Docker volume | high |
| Object/structured store | Filesystem adapter | Preserve source and structured extraction artifacts | `app/ingestion/object_store.py`, loaders | Files under `storage/` | Host filesystem | high |
| Embedding service | Model service | Produce local query/document embeddings | `app/ingestion/embedder.py`, `docker-compose.yml` | Model weights/runtime cache | Provisioned model artifacts | high |
| LLM service | Model service | Produce grounded answers | `app/agents/generation.py`, `docker-compose.yml` | Model weights/KV cache | llama.cpp, GGUF artifact | high |
| Retrieval | Domain service | PostgreSQL FTS, pgvector ANN, hybrid fusion and reranking | `app/retrieval/` | Reads PostgreSQL state | Embedder, PostgreSQL | high |
| Observability/security | Cross-cutting | Trace IDs, timings, metrics, API-key/rate controls | `app/observability/`, `app/core/security.py` | Mostly process-local | FastAPI | high |

## Primary request and async flows

### Research query
- Entrypoint: `POST /api/v1/research/query` and streaming variant.
- Modules involved: research graph, query embedder, retrieval, reranker, answer generator, citation verifier, session repository.
- State stores touched: PostgreSQL chunks/vectors/sessions/checkpoints.
- External services called: local embedding and LLM HTTP services; web search is disabled in offline mode.
- Async work produced or consumed: Request-scoped async calls; generation admission is bounded.
- Trust boundaries crossed: Untrusted client input, API-key middleware, local model HTTP, persisted conversation state.
- Blast radius: PostgreSQL or embedding failure disables retrieval; LLM failure disables generation but not stored documents.
- Evidence: `app/agents/service.py`, `app/retrieval/service.py`, `app/agents/generation.py`.
- Confidence: high.

### Queued document ingestion
- Entrypoint: Admin ingestion API/CLI -> `ingestion_jobs` -> `citebot-worker`.
- Modules involved: loader/OCR, normalizer, chunker, embedder, repositories, pgvector writer.
- State stores touched: Filesystem artifacts plus PostgreSQL document/chunk/vector/job tables.
- External services called: Local embedding HTTP; OCR libraries execute in worker.
- Async work produced or consumed: One leased job at a time in the default worker.
- Trust boundaries crossed: Uploaded/user-selected files, parser libraries, model HTTP.
- Blast radius: A worker failure can leave relational chunks present before vector upsert; retry/reindex is required to reconcile.
- Evidence: `app/ingestion/service.py`, `app/ingestion/repository.py`, `app/ingestion/vector_writers.py`.
- Confidence: high.

### Hybrid retrieval
- Entrypoint: Admin search and research retrieval calls.
- Modules involved: `DatabaseSparseRetriever`, `PgVectorDenseRetriever`, fusion, reranker.
- State stores touched: PostgreSQL chunks/documents and `chunk_embeddings`.
- External services called: Local embedding service for the query.
- Async work produced or consumed: Parallelism is request-scoped; backend calls are currently sequenced.
- Trust boundaries crossed: Stored text/query sent only to the local embedding service.
- Blast radius: One PostgreSQL outage removes both sparse and dense retrieval, trading redundancy for simpler consistency and operations.
- Evidence: `app/retrieval/service.py`.
- Confidence: high.

## State stores and data ownership

| Store/model/table | Owner or module | Used by | Access pattern | Coupling/risk | Evidence |
| --- | --- | --- | --- | --- | --- |
| Documents/chunks/projects | SQL repositories | API, worker, retrieval | Keyed writes, scoped reads, joins | Central availability dependency | `app/db/models.py`, repositories |
| `chunk_embeddings` | PgVector writer/retriever | Worker, retrieval | Idempotent upsert, HNSW cosine query | Separate transaction from chunk write | `app/ingestion/vector_writers.py` |
| `ingestion_jobs` | Ingestion repository | API, worker | Lease claim, heartbeat, recovery | Queue and database share failure domain | `app/ingestion/repository.py` |
| LangGraph checkpoints | Database checkpointer | Workflow review graph | Thread/checkpoint keyed blobs | Serialized payload compatibility | `app/workflows/checkpointer.py` |
| Raw/structured files | Object-store/loader | Worker, citation/export paths | Local file reads/writes | Must be backed up consistently with DB | `app/ingestion/object_store.py` |

## External dependencies

| Dependency | Purpose | Call sites/config | Sync or async | Failure impact | Boundary/adapter | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| PostgreSQL/pgvector | All durable query/application state | `DATABASE_URL` | async | API/worker readiness fails | SQLAlchemy/session manager | `app/db/session.py` |
| Embedding service | Document/query embeddings | `EMBEDDING_BASE_URL` | async HTTP | Ingestion and dense retrieval fail | `BaseEmbedder` adapter | `app/ingestion/embedder.py` |
| llama.cpp | Answer generation | `LLM_BASE_URL` | async HTTP | Research generation fails | Answer generator adapter | `app/agents/generation.py` |
| OCR/parser libraries | Document extraction | Worker configuration | local sync work behind async worker | Specific documents fail/quarantine | Loader/OCR adapters | `app/ingestion/loaders.py` |

## Trust boundaries
- Inbound clients and public APIs: Caddy/FastAPI accept queries and uploads; validation and size/type limits apply.
- Auth/session boundaries: Separate research/admin API keys; conversations persist in PostgreSQL.
- Admin/internal boundaries: Ingestion, evaluation, and document management routes require admin policy when enabled.
- Tenant/data isolation boundaries: Project IDs scope repository and retrieval predicates; this is project isolation, not multi-user authorization.
- Webhook or third-party callback boundaries: None found in the offline runtime.
- File/object storage boundaries: Untrusted documents cross into local parsers/OCR and persist under `storage/`.
- Queue/cache boundaries: PostgreSQL is the durable queue; process-local metrics/rate state is not shared across replicas.

## Architectural strengths
- One production database removes Qdrant/SQLite dual-write and schema-drift paths (`docker-compose.yml`, lifecycle and retrieval wiring).
- Sparse and dense retrieval use the same project/document metadata joins, reducing filter divergence (`app/retrieval/service.py`).
- Startup DDL is serialized with PostgreSQL advisory locks (`app/db/session.py`, `app/ingestion/vector_writers.py`).
- Offline policy and local-service allowlisting fail fast in production settings (`app/core/config.py`).
- LLM timing captures queue, prompt, generation, throughput, and provider request IDs (`app/agents/generation.py`, metrics).

## Architectural risks

### ARCH-001: Legacy-state cutover is not automated
- Severity: high
- Category: data ownership
- Evidence: New Compose uses PostgreSQL only; no SQLite/Qdrant import process exists.
- Why it matters: A new deployment is healthy but empty unless content is imported or re-ingested.
- Likely blast radius: Historical projects, jobs, sessions, and vectors from the old stores.
- Recommended action: Treat release as a cold migration; preserve backups and choose explicit import versus clean re-index before cutover.
- Confidence: high

### ARCH-002: Chunk and embedding writes are not one atomic unit
- Severity: medium
- Category: coupling
- Evidence: Repository and pgvector writer open separate transactions in ingestion.
- Why it matters: Partial success can make sparse results available while dense results are missing.
- Likely blast radius: The current document/version and dense/hybrid retrieval quality.
- Recommended action: Add an indexing-state marker or share one transaction/unit of work.
- Confidence: high

### ARCH-003: One database is a shared availability boundary
- Severity: medium
- Category: deployment
- Evidence: Sessions, queue, FTS, vectors, and application tables all use PostgreSQL.
- Why it matters: Consolidation simplifies consistency but PostgreSQL failure affects all durable workflows.
- Likely blast radius: API readiness, ingestion, retrieval, sessions, and review checkpoints.
- Recommended action: Add tested PostgreSQL backup/restore, disk alerts, connection monitoring, and recovery objectives.
- Confidence: high

## Operational maturity observations
- Health/readiness: Database, retrieval, storage, and local model checks are wired; a live consolidated-stack test is pending.
- Graceful shutdown: Container lifecycle disposes services and SQLAlchemy engine; worker handles interruption.
- Config and secrets: Environment-driven settings validate offline/local constraints; database password is externalized.
- Logging: Structured request/trace logging exists.
- Metrics/tracing: Request and detailed LLM latency metrics exist; PostgreSQL query/pool telemetry is limited.
- Timeouts/retries: Model HTTP and worker retry/lease controls exist; DB statement/lock timeouts are not explicit.
- Rate limiting/backpressure: API rate limits, bounded generation admission, and one worker constrain load.
- Idempotency: Content hashes, stable chunk IDs, upserts, and durable job state reduce duplicate work.
- Deploy/rollback signals: Readiness and test harness exist; cross-store migration/rollback is not automated.

## Recommended refactoring priorities
1. Perform and document a cold PostgreSQL cutover rehearsal with count and retrieval validation.
2. Make relational chunk and pgvector persistence atomic or explicitly reconciled.
3. Add PostgreSQL pool/query metrics, timeouts, disk monitoring, and restore testing.
4. Move future large DDL/backfills out of application startup.

## Unknowns and confidence
- Unknown: Legacy data-retention requirements, real corpus size, filtered HNSW/GIN plans, production concurrency, and consumer-GPU model configuration.
- Why it matters: These determine migration scope, performance envelope, and whether GPU acceleration is actually active.
- How to verify safely: Rehearse with a copied legacy dataset and provisioned models on the target host; run integration, benchmark, restart, backup/restore, and GPU telemetry checks before deleting old volumes.
