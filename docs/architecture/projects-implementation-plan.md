# Project-Scoped Documents and Queries — Implementation Plan

> Status: implemented. This document records the delivered project-scoping
> contract; the bundled Sample Project is the only deterministic workspace.

## 1. Outcome

Introduce projects as the required workspace boundary in CiteBot:

- every uploaded document belongs to exactly one project;
- every document query searches all current documents in exactly one project;
- conversations, ingestion jobs, analysis runs, evidence, and generated work products remain attached to that project;
- a project can be reused by multiple team members without repeatedly uploading or re-sending the same source set; and
- a deterministic **Sample Project** is created on first run, populated from the bundled sample corpus, and shown as **Ready to query** once ingestion is complete.

The project boundary must be enforced in repositories and retrieval backends, not only in the browser. A missing or unknown project must fail closed rather than falling back to the global corpus.

## 2. Scope and product decisions

### In scope

- Project creation, listing, viewing, renaming, and archiving.
- Project-scoped document upload, listing, versions, and ingestion status.
- Project-scoped chat, conversation history, retrieval, citations, analysis runs, and workflow execution.
- A first-run Sample Project backed by `data/sample_corpus`.
- Migration of existing global data into the Sample Project when no project
  scope exists yet.
- Consistent project filtering in SQLite metadata, FTS5, local dense retrieval, pgvector, and Qdrant.
- Browser flows for selecting a project, creating a project, uploading sources, and querying the selected project.

### Explicitly deferred

- User accounts, invitations, roles, and per-member permissions.
- A document shared by several projects through a many-to-many relationship.
- Moving a document between projects or cloning a project.
- Permanent project deletion and cascading removal of evidence/audit records.

The current application has global API-key access rather than user identity. In this release, a project is a corpus and collaboration boundary, not a security tenant. Team-level authorization can be added later without changing the project-scoped data model. Until then, anyone with workspace access can see all projects.

## 3. Core invariants

1. `project_id` is required for every newly created document, ingestion job, research session, and analysis run.
2. A query can retrieve only chunks whose document belongs to the query's project.
3. Project scope is injected by the server. Request-provided filters may narrow results inside a project but can never broaden or replace its scope.
4. A conversation cannot be continued from another project. A mismatched `(project_id, session_id)` returns `404` to avoid disclosing its existence.
5. A document's logical identity is project-local. The same source may be uploaded independently to two projects without ID or uniqueness collisions.
6. Archived projects are read-only: prior conversations and evidence remain inspectable, but uploads and new queries are rejected.
7. The Sample Project uses a stable ID and idempotent seed operation, so restarts never create duplicates.
8. “Ready to query” means the project has at least one current indexed document and has no active initial seed job. Empty or failed projects must not claim readiness.

## 4. Target data model

### `projects`

Add `ProjectRecord` in `app/db/models.py`:

| Field | Type / constraint | Purpose |
| --- | --- | --- |
| `project_id` | `String(64)`, primary key | Stable UUID for normal projects; deterministic constant for system projects. |
| `name` | `String(255)`, required | Display name. |
| `slug` | `String(255)`, unique, required | Human-readable stable locator if URLs use slugs later. |
| `description` | `Text`, nullable | Project purpose and source-set description. |
| `status` | `String(32)`, indexed | `active` or `archived`. Readiness is computed, not stored. |
| `is_sample` | `Boolean`, indexed, default false | Identifies the bundled Sample Project. |
| `created_at` / `updated_at` | timezone-aware timestamps | Sorting and audit metadata. |

Use one deterministic ID:

- `sample-project` for the bundled sample corpus;

### Ownership columns

Add an indexed foreign key `project_id -> projects.project_id` to:

- `documents`;
- `ingestion_jobs`;
- `research_sessions`; and
- `analysis_runs`.

Add direct `project_id` to work products only if project-level work-product listing cannot efficiently and safely join through `analysis_runs`; otherwise keep `analysis_run_id` as the authoritative relationship and always join through it. Claims, evidence, anchors, chunks, and versions inherit scope through their parent document or analysis run and do not need redundant project columns in the relational database.

Change document uniqueness from global `source_uri` to `(project_id, source_uri)`. Update normalized document IDs from:

```text
uuid5(NAMESPACE_URL, source_uri)
```

to:

```text
uuid5(NAMESPACE_URL, project_id + ":" + source_uri)
```

This also namespaces chunk IDs, object-store paths, versions, and vector records because they derive from the document ID.

### API schemas

Add `app/projects/schemas.py` with:

- `ProjectCreate(name, description?)`;
- `ProjectUpdate(name?, description?, status?)`;
- `ProjectSummary(project_id, name, description, status, is_sample, document_count, ready_document_count, processing_document_count, failed_job_count, readiness, created_at, updated_at)`;
- `ProjectDetail` with the same counters and optional latest activity; and
- `ProjectReadiness = empty | preparing | ready | failed | archived`.

Add `project_id` to `DocumentSummary`, `JobStatusResponse`, `ResearchSessionRecord`, `ConversationSummary`, and `ResearchResponse`. Internally, add a required `project_id` to the canonical document/ingestion context and research agent state.

## 5. API contract

Use nested project routes as the canonical public contract:

```text
GET    /api/v1/projects
POST   /api/v1/projects
GET    /api/v1/projects/{project_id}
PATCH  /api/v1/projects/{project_id}
DELETE /api/v1/projects/{project_id}              # archive, not hard delete

GET    /api/v1/projects/{project_id}/documents
POST   /api/v1/projects/{project_id}/documents/uploads?filename=...
GET    /api/v1/projects/{project_id}/documents/jobs
GET    /api/v1/projects/{project_id}/documents/{document_id}/versions

GET    /api/v1/projects/{project_id}/conversations
GET    /api/v1/projects/{project_id}/conversations/{session_id}
DELETE /api/v1/projects/{project_id}/conversations/{session_id}

POST   /api/v1/projects/{project_id}/research/query
POST   /api/v1/projects/{project_id}/research/query/stream
GET    /api/v1/projects/{project_id}/research/runs/{analysis_run_id}/evidence
```

Apply the same nesting or mandatory project dependency to analysis and workflow routes that can retrieve documents or expose derived outputs.

### Compatibility policy

- Nested project routes are the canonical contract and are used by the browser.
- Existing unscoped callers are retained as a compatibility shim targeting the
  Sample Project; they never search a global corpus.
- Update first-party scripts and integrations to send an explicit project ID
  whenever a project other than the Sample Project is intended.

### Error behavior

- `404 project_not_found` for unknown or inaccessible projects.
- `409 project_slug_conflict` for duplicate slugs.
- `409 project_archived` for uploads or queries against archived projects.
- `404 document_not_found` when the document exists outside the requested project.
- `404 conversation_not_found` for a cross-project session ID.

## 6. Repository and service changes

### Project module

Create:

```text
app/projects/__init__.py
app/projects/schemas.py
app/projects/repository.py
app/projects/service.py
app/api/routes/projects.py
```

`ProjectRepository` owns CRUD, slug allocation, archive state, aggregate document/job counts, and deterministic `ensure_system_project(...)`. `ProjectService` validates active state before mutation/query operations.

### Ingestion

Update `app/ingestion/service.py`, `repository.py`, `schemas.py`, `normalizer.py`, and the upload/admin routes:

1. Require `project_id` in `ingest_path`, `enqueue_path`, `_process_job`, and `create_job`.
2. Persist the project on a job before the worker can claim it; include it in `JobStatusResponse` so a queued worker cannot lose scope.
3. Resolve idempotency with `(project_id, source_path, content/index versions)`, not `source_path` alone.
4. Look up existing document state with `(project_id, source_uri)`.
5. Normalize IDs with the project namespace.
6. Store browser uploads under `uploads/{project_id}/{upload_id}` after validating the project is active.
7. Filter document, job, and version listings by project and verify parent ownership on every detail lookup.

Admin path ingestion must also require a project. “Admin” is an authorization scope, not permission to put documents into a global corpus.

### Retrieval

Add a required scalar `project_id` to the internal `RetrievalFilters`. Do not expose it as an optional user-controlled list.

- `RetrievalRepository.list_chunks`: join `documents` and add `DocumentRecord.project_id == filters.project_id` before all optional filters.
- Local dense retrieval: inherits the repository predicate.
- SQLite FTS5: add `project_id NOT NULL` to `sparse_chunks`, index it, include it in inserts/migration, and add `c.project_id = ?` to every search.
- pgvector: join `documents` and filter by its project ID; optionally copy project ID into `chunk_embeddings` only if benchmarks justify denormalization.
- Qdrant: include `project_id` in every point payload, create a payload index, and include an exact-match project condition in every query filter.
- Hybrid search/reranking: preserve the same project filter in every decomposed query and backend retry/fallback.

The research agent must construct its own `SearchRequest` with the route-validated project ID. This closes the current gap where `_hybrid_retrieval` creates a filterless request for each decomposed query.

### Conversations and evidence

- Change session-store methods to `get(project_id, session_id)`, `save(record)`, `list(project_id)`, and `delete(project_id, session_id)`.
- Reject an existing session if its stored project differs from the current route.
- Save `project_id` on `analysis_runs`; require it when retrieving evidence ledgers and work products.
- Include project scope in logs and trace metadata, but do not put document contents into logs.
- Ensure web-search results, when explicitly enabled, are supplemental contexts and are never persisted as project documents unless separately uploaded.

## 7. Database and index migration

Implement a forward-only `002_projects` migration in `app/db/migrations/` and extend migration tests for both SQLite and PostgreSQL behavior.

Migration order:

1. Create `projects`.
2. Insert the deterministic Sample Project row idempotently.
3. Add nullable `project_id` columns to documents, jobs, sessions, and runs.
4. Backfill all existing rows to `sample-project`.
5. Add indexes and foreign keys where supported.
6. Replace the global `documents.source_uri` uniqueness constraint with `(project_id, source_uri)`.
7. Rebuild affected SQLite tables where required to enforce `NOT NULL` and the new unique constraint; use an explicit transaction and preserve indexes/foreign keys.
8. Mark the migration applied only after validation queries report zero null or orphaned project IDs.

Before applying the migration to a real installation, create a cold database backup and record the existing sparse/Qdrant index versions. The migration is additive for document content, but changing identity and index payloads requires a controlled reindex.

### Reindex strategy

Existing document IDs can remain stable inside the Sample Project during the
upgrade window, provided all new chunks receive `project_id` and all retrieval
paths filter it. New or re-uploaded documents use project-namespaced IDs.
Rebuild the FTS index and upsert Qdrant payloads from relational metadata
before enabling project-scoped queries. Do not mark a migrated project ready
until all current chunks have a project payload.

For rollback, retain the pre-migration database and old versioned indexes. Application rollback uses those artifacts together; do not attempt a mixed-schema rollback.

## 8. Sample Project bootstrap

Replace the current path-only sample bootstrap with this idempotent sequence:

1. Call `ensure_system_project(project_id="sample-project", name="Sample Project", is_sample=True)` during lifespan startup.
2. Check for ready documents or an active/completed sample job scoped to that project.
3. Queue or run `data/sample_corpus` ingestion with `project_id="sample-project"`.
4. Compute readiness from project documents/jobs; do not persist a manually toggled “ready” flag.
5. On retry after a failure, reuse the same project and create a new job without duplicating unchanged documents.

The project list always includes the Sample Project. Its UI state is:

- `Preparing sample project…` while the initial job is queued/running;
- `Sample Project · Ready to query` after at least one document is indexed and the seed job completes;
- `Sample Project · Setup failed` with a retry action if ingestion fails.

For synchronous test mode, the first project-list response after startup must show `ready`. For queued production mode, readiness may transition from `preparing` to `ready`; the browser polls while preparing and enables the query composer only when ready.

## 9. Browser experience

Update `app/web/index.html`, `app/web/app.js`, and `app/web/styles.css` around a selected-project state:

1. On load, fetch `/projects` before documents or conversations.
2. Restore `citebot.selectedProjectId` only if it still exists; otherwise select Sample Project when ready, then the first active project.
3. Add a project switcher to the header/sidebar with name, document count, and readiness badge.
4. Add a Projects view containing project cards and a minimal “New project” dialog.
5. Scope Chat and Documents labels to the selected project, for example `Ask Sample Project` and `Documents in Sample Project`.
6. Pass the selected project in every document, conversation, and research URL.
7. On project switch, clear the active session, citations, messages, pending polling state, and document cache before loading the new project.
8. Disable upload/query controls for archived, empty, or preparing projects as appropriate. Empty active projects allow uploads but show “Add documents to start querying.”
9. Feature the seeded card with a `Sample` label and the exact ready copy `Ready to query`.

Avoid merging this work over the current uncommitted authentication/UI edits without first reconciling them. The implementation should preserve those user-owned changes and modify only the necessary surrounding lines.

## 10. Delivery phases

### Phase 0 — Contract and migration safety

- Add project schemas and document the nested API contract.
- Add migration fixtures representing a pre-project database.
- Add a database backup/restore migration test.
- Freeze the readiness definitions and compatibility behavior.

**Exit:** an old database can be migrated in a test, all old rows resolve to a
valid project scope, and the migration is idempotent.

### Phase 1 — Project persistence and API

- Add `ProjectRecord`, repository, service, routes, and service-container wiring.
- Implement create/list/get/update/archive and aggregate readiness.
- Add unit/API tests for slug conflicts, archive behavior, stable system projects, and counts.

**Exit:** projects are durable and independently manageable.

### Phase 2 — Project-scoped ingestion and indexes

- Thread project ID through upload, admin ingestion, queue workers, normalization, storage, metadata, and document/version/job listings.
- Update FTS5, Qdrant, pgvector, and local retrieval payloads/predicates.
- Build a reindex command/check that reports chunks missing project scope.

**Exit:** two projects can ingest identical source paths, and neither can retrieve or list the other's documents.

### Phase 3 — Project-scoped research and workflows

- Add nested research and conversation routes.
- Bind sessions, decomposed retrieval queries, analysis runs, evidence ledgers, calculations, diffs, and workflow outputs to the validated project.
- Update evaluation and CLI callers.

**Exit:** cross-project session reuse and run/evidence access fail closed; all citations originate from the selected project unless explicitly marked external web context.

### Phase 4 — Sample Project

- Create the deterministic system project during startup.
- Seed the bundled corpus into it with project-aware idempotency and retries.
- Add readiness aggregation, logs, and bootstrap tests for first run, restart, active job, completed job, and failed retry.

**Exit:** a clean install presents exactly one Sample Project and it becomes `Ready to query` with bundled sources searchable.

### Phase 5 — Browser and documentation

- Add project list/switch/create/archive flows and scoped empty/loading/error states.
- Select the ready Sample Project by default on a clean install.
- Update README, deployment guide, Postman collection, scripts, and API examples.

**Exit:** the complete create project → upload → ready → query → revisit conversation flow works from the browser, and the sample flow works without setup.

### Phase 6 — Hardening and cutover

- Run migration, API, retrieval isolation, restart, streaming, and browser tests.
- Reindex each backend and validate that no chunk lacks project scope.
- Add metrics for project counts, project-scoped query failures, bootstrap status, and missing-scope rejections.
- Keep the nested project routes as the canonical contract; compatibility
  callers target the Sample Project until they adopt an explicit project URL.

**Exit:** project isolation tests pass against every enabled retrieval backend, migration rollback has been rehearsed, and unscoped queries cannot execute.

## 11. Test plan

### Migration and persistence

- Existing documents/jobs/sessions/runs are assigned to the Sample Project when
  no prior project scope exists.
- Migration is idempotent and preserves counts, versions, anchors, and conversations.
- The same `source_uri` can exist once in Project A and once in Project B.
- Project archive preserves derived evidence and blocks new writes.

### Isolation

- Upload distinct “red” and “blue” fixtures to two projects; each query returns only its own sentinel.
- Repeat isolation tests for sparse, local dense, pgvector, Qdrant, hybrid, decomposed-query, reranking, and fallback paths.
- Supplying Project B document IDs as optional filters to a Project A query returns no results.
- A Project A session/run/document ID cannot be fetched through Project B routes.

### Ingestion

- Uploads without a project are rejected.
- Queued jobs preserve project scope across worker restart and stale-lease recovery.
- Re-ingestion is idempotent within a project but independent across projects.
- Job and document counters/readiness transition correctly for success, partial failure, retry, and empty corpora.

### Sample bootstrap

- Clean install creates one Sample Project and ingests `data/sample_corpus`.
- Restart does not create another project or duplicate job/documents.
- A concurrent API/worker startup schedules at most one active seed job.
- Completed seed yields `is_sample=true`, `readiness=ready`, and a successful cited sample query.
- Failed seed is visible and retryable rather than incorrectly shown as ready.

### Browser/API contract

- Switching projects clears conversation and citation state.
- Document and conversation lists refresh only for the selected project.
- The query composer targets the selected project in both normal and streaming modes.
- Empty, preparing, ready, archived, and failed states have correct controls and accessible text.
- Canonical project routes never search outside the project in their URL.

## 12. Acceptance criteria

The feature is complete when:

1. A user can create a project, upload multiple documents only within that project, and query across all ready documents in it.
2. Results, citations, conversations, evidence, and workflow outputs cannot cross project boundaries in any enabled backend.
3. The same physical/logical source can be ingested independently into two projects.
4. Existing installations migrate without losing documents or conversations; old unscoped rows resolve to the Sample Project.
5. A clean installation automatically creates exactly one Sample Project populated from the bundled corpus.
6. The Sample Project is visibly labeled `Sample` and `Ready to query` after bootstrap, is selected by default when appropriate, and returns a cited answer without a user upload.
7. Restarting the API or worker does not duplicate the sample project, seed job, or documents.
8. No unscoped upload or query path can access the global corpus.

## 13. Recommended implementation order by file

```text
1. app/db/models.py
   app/db/migrations/__init__.py
   app/projects/{schemas,repository,service}.py

2. app/core/lifecycle.py
   app/core/dependencies.py
   app/api/routes/projects.py
   app/api/routes/__init__.py

3. app/ingestion/{schemas,normalizer,repository,service,bootstrap}.py
   app/api/routes/{documents,admin_ingestion}.py
   app/ingestion/{sparse_index,vector_writers}.py

4. app/retrieval/{repository,service}.py
   app/agents/{schemas,service,session_store}.py
   app/api/routes/{research,conversations,analysis,workflows}.py
   app/evidence/repository.py

5. app/web/{index.html,app.js,styles.css}
   scripts and evaluation harness callers
   README.md, deployment docs, Postman collection

6. tests/test_projects_api.py
   tests/test_project_isolation.py
   tests/test_project_migration.py
   existing ingestion/research/workspace/contract/bootstrap tests
```

This order establishes the database and server-side scope before exposing the project selector, preventing a UI state in which projects appear isolated while retrieval remains global.
