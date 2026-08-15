# Database Performance Audit

## Executive summary
CiteBot now has one production database: PostgreSQL with pgvector. Relational
state, the ingestion queue, LangGraph checkpoints, full-text search, and dense
embeddings share that service. The previous SQLite FTS5 and Qdrant runtime paths
have been removed. The main remaining performance risks are project-summary N+1
aggregates, row-at-a-time embedding upserts, and unconfigured PostgreSQL pool and
statement limits. No production latency or query-plan evidence was available.

## Schema and access-layer overview
- DB engine(s): PostgreSQL/pgvector in shipped runtime; SQLite is retained only as a dependency-free test dialect.
- ORM/query builder: Async SQLAlchemy ORM plus parameterized SQL for PostgreSQL FTS and pgvector operators.
- Migration tooling: In-process, forward-only migration registry and SQLAlchemy metadata creation.
- Connection pooling: SQLAlchemy async engine defaults with `pool_pre_ping`; no explicit pool budget or statement timeout.
- Read/write separation: None.
- Queue/outbox tables: `ingestion_jobs` is a lease-based durable queue; no outbox was found.
- Evidence: `app/db/session.py`, `app/db/migrations/__init__.py`, `app/ingestion/repository.py`, `app/retrieval/service.py`, `app/ingestion/vector_writers.py`, `docker-compose.yml`.

## Top query risks

### DBQ-001: Project summaries execute repeated aggregate queries
- Severity: medium
- Category: N+1
- Evidence: `app/projects/repository.py:list` invokes `_summary` for each project, and `_summary` issues multiple aggregate queries.
- Observed or inferred: Query shape is observed; operational impact is inferred because project counts are unknown.
- Why it matters: Project-list query count grows linearly with project count.
- Likely symptom: Increasing workspace-list latency and database round trips.
- Recommended remediation: Produce counts/status with grouped subqueries or one aggregate statement.
- Effort: medium
- Confidence: high

### DBQ-002: Embeddings are upserted one row per round trip
- Severity: medium
- Category: hot write
- Evidence: `PgVectorWriter.upsert_chunks` awaits one `INSERT ... ON CONFLICT` for each chunk.
- Observed or inferred: Observed implementation; throughput impact is inferred.
- Why it matters: Large documents multiply SQL executions and transaction duration.
- Likely symptom: Slow ingestion and longer-lived write transactions.
- Recommended remediation: Use a bounded executemany/bulk insert or staging table while retaining idempotent conflict handling.
- Effort: medium
- Confidence: high

### DBQ-003: Test-only local dense fallback scans and re-embeds candidates
- Severity: low
- Category: scan
- Evidence: `LocalDenseRetriever` loads up to `MAX_LOCAL_DENSE_CANDIDATES` and embeds them in process; Compose disables automatic fallback.
- Observed or inferred: Observed and bounded.
- Why it matters: Enabling the fallback on a non-test corpus consumes avoidable CPU and memory.
- Likely symptom: Dense-query latency spikes.
- Recommended remediation: Keep it disabled in runtime profiles and preserve the hard candidate cap.
- Effort: low
- Confidence: high

## Indexing findings

### IDX-001: Unified retrieval has purpose-built FTS, HNSW, and queue indexes
- Severity: informational
- Evidence: migration `004_postgres_retrieval_indexes` adds GIN and queue indexes; `PgVectorWriter.initialize` adds HNSW and embedding-version lookup indexes.
- Access pattern affected: Sparse retrieval, nearest-neighbor retrieval, queued-job claim, and stale lease recovery.
- Recommendation: Capture `EXPLAIN (ANALYZE, BUFFERS)` on representative corpus sizes and verify HNSW and GIN selection.
- Caveats: No live PostgreSQL instance or representative data was available in this workspace.
- Confidence: high

## Transaction findings

### TX-001: Relational chunks and vector rows commit in separate transactions
- Severity: medium
- Evidence: `IngestionService` saves relational content before `PgVectorWriter.upsert_chunks`, and each dependency opens its own session.
- Lock or consistency risk: A vector-write failure can leave searchable FTS chunks without dense embeddings until retry/reindex.
- Recommended remediation: Record an explicit indexing state or move both writes into one shared unit of work after pgvector consolidation.
- Confidence: high

### TX-002: Startup DDL is serialized across API and worker
- Severity: informational
- Evidence: PostgreSQL transaction advisory locks in `DatabaseSessionManager.initialize` and `PgVectorWriter.initialize`.
- Lock or consistency risk: Startup waits rather than racing on metadata, migration, extension, or index creation.
- Recommended remediation: Keep the locks; move long future migrations to a one-shot migration job.
- Confidence: high

## Connection-pool concerns
- Finding: Pool size, overflow, acquisition timeout, statement timeout, and lock timeout are not configured explicitly.
- Evidence: `DatabaseSessionManager` passes only `pool_pre_ping` to `create_async_engine`.
- Risk under load: API and worker default pools may exceed the intended single-host connection budget or wait indefinitely on expensive statements.
- Recommendation: Add bounded per-process pool settings and PostgreSQL statement/lock timeouts after measuring the normal concurrency envelope.

## ORM-specific concerns
- Finding: Project summaries have an aggregate N+1 pattern; most other repository reads are bounded or key-based.
- Evidence: `app/projects/repository.py:list` and `_summary`.
- Production risk: Linear query growth as project count increases.
- Recommendation: Replace loop-driven aggregates with grouped SQL.

## Remediation priorities
1. Run live PostgreSQL integration and query-plan tests for FTS, filtered HNSW, queue claim, and lease recovery.
2. Make chunk metadata and embedding persistence an observable unit of work.
3. Batch pgvector upserts.
4. Collapse project-summary aggregates and configure bounded pools/timeouts.

## Confidence and unknowns
- Unknown: Production corpus size, project count, queue depth, write concurrency, and PostgreSQL execution plans.
- Why it matters: These determine whether inferred query and pool risks are material.
- How to verify safely: Restore or ingest a representative corpus into an isolated PostgreSQL instance, run the retrieval harness, and capture plans/slow-query metrics without weakening durability.
