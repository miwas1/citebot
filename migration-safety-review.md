# Migration Safety Review

## Migration inventory

| Migration/script | Operation summary | Tables/models affected | Data movement | Risk level | Evidence |
| --- | --- | --- | --- | --- | --- |
| SQLAlchemy metadata initialization | Creates the unified relational schema | All ORM models | None | medium | `app/db/session.py` |
| `001_foundation` | Verifies foundation tables | Evidence/work-product tables | None | low | `app/db/migrations/__init__.py` |
| `002_projects` | Creates Sample Project and scopes legacy rows | Projects, documents, jobs, sessions, runs | Backfills project IDs | high | `app/db/migrations/__init__.py` |
| `003_retire_legacy_project` | Renames the legacy project scope | Project-scoped tables | Moves legacy project references | medium | `app/db/migrations/__init__.py` |
| `004_postgres_retrieval_indexes` | Adds GIN FTS and queue indexes | Chunks, ingestion jobs | Builds indexes | medium | `app/db/migrations/__init__.py` |
| pgvector initialization | Creates extension, vector table, HNSW/filter indexes and validates dimensions | `chunk_embeddings` | Builds indexes | medium | `app/ingestion/vector_writers.py` |
| SQLite/Qdrant cutover | Starts a new PostgreSQL source of truth | All runtime state | Requires explicit import or source re-ingestion | high | `docker-compose.yml`, current deployment docs |

## Unsafe migration patterns

### MIG-001: Existing SQLite and Qdrant state is not automatically imported
- Severity: high
- Evidence: Runtime configuration now selects PostgreSQL and Qdrant/standalone SQLite adapters were removed; no cross-store importer exists.
- Observed or inferred: Observed.
- Blocking/locking risk: None, but a new PostgreSQL volume starts empty.
- Backward-compatibility risk: Existing conversations, job history, and document metadata are not visible after cutover.
- Data-loss risk: High if old volumes/files are deleted before verification; source documents can be re-ingested, but sessions/history may not be reconstructable.
- Safer alternative: Cold-back up `storage/`, the old SQLite files, and Qdrant volume; either implement and dry-run an importer or accept a documented clean re-index while retaining the backup.
- Confidence: high

### MIG-002: Retrieval indexes are built during application startup
- Severity: medium
- Evidence: migration `004` and `PgVectorWriter.initialize` execute index DDL during container initialization.
- Observed or inferred: Observed; lock duration is unknown.
- Blocking/locking risk: GIN/HNSW builds can delay startup and block conflicting DDL/writes on a populated database.
- Backward-compatibility risk: A long index build can make readiness fail during rolling deployment.
- Data-loss risk: Low.
- Safer alternative: For populated upgrades, create large indexes in a dedicated migration step (concurrently where supported), then deploy application processes.
- Confidence: high

### MIG-003: Application and worker share migration responsibility
- Severity: low
- Evidence: Both initialize the same database, but transaction advisory locks now serialize schema and pgvector DDL.
- Observed or inferred: Observed.
- Blocking/locking risk: One process waits for the other during bootstrap.
- Backward-compatibility risk: Low for current additive migrations.
- Data-loss risk: Low.
- Safer alternative: Retain advisory locks now and introduce a one-shot migration service before any future backfill or table rewrite.
- Confidence: high

## Deployment ordering risks
- Risk: Starting the new Compose stack before preserving legacy state creates an apparently healthy but empty installation.
- Evidence: PostgreSQL is now mandatory and uses a new named volume; no importer is called at startup.
- Required order: Stop writers, back up legacy database/vector/files, choose import or clean re-index, start PostgreSQL, initialize/migrate once, ingest/import, validate counts and retrieval, then start normal traffic.
- Rolling deploy compatibility: This is a cold cutover, not a mixed-version rolling deployment; old code expects SQLite/Qdrant while new code expects PostgreSQL.

## Rollback risks
- Risk: New writes in PostgreSQL diverge from the preserved legacy stores.
- Evidence: There is no dual-write path by design.
- What breaks on rollback: Documents/jobs/sessions created after cutover disappear from the old application view.
- Safer rollback/roll-forward plan: Keep the old stack read-only, define a cutover window, and prefer roll-forward; if rollback is required, export post-cutover data or accept the explicitly measured recovery point.

## Safer rollout plan
1. Stop the old API and worker and take verified backups of `storage/`, SQLite files, and the Qdrant volume.
2. Decide whether session/job history must be retained; if yes, implement and dry-run an explicit importer before production cutover.
3. Start only PostgreSQL and run schema/pgvector initialization once.
4. Re-ingest retained source files or import relational data, regenerating embeddings with the configured model/version.
5. Compare document/chunk counts and run sparse, dense, and hybrid retrieval checks.
6. Start API/worker traffic, monitor failures/latency, and retain the old backup until the recovery window expires.

## Pre-release validation checklist
- Schema diff reviewed: yes, statically.
- Explain plans or query review for hot paths: query review complete; live plans pending.
- Migration dry run on production-like volume: pending.
- Backfill batching and resume behavior: no automatic cross-store backfill exists; re-ingestion is job-based and retryable.
- Lock timeout / statement timeout strategy: advisory startup locks exist; statement/lock timeouts remain pending.
- App compatibility before and after migration: unit suite passes; mixed old/new rolling deployment is unsupported.
- Rollback or roll-forward tested: pending.
- Monitoring and alert thresholds: request/LLM timing exists; PostgreSQL pool/slow-query thresholds remain pending.
