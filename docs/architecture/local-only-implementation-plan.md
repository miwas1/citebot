# Local-Only CiteBot Implementation Plan

## 1. Outcome

Pivot CiteBot from a locally runnable scaffold with optional hosted providers into a genuinely offline-capable, privacy-first document RAG system. The shipped runtime must be able to boot, ingest supported local documents, selectively OCR, embed, index, retrieve, rerank, generate answers, and verify citations with no outbound network access.

The rough draft is the default product direction:

- FastAPI orchestration.
- PyMuPDF native PDF extraction before OCR.
- PaddleOCR + PP-StructureV3 as the primary OCR/layout path.
- Tesseract as a low-confidence fallback.
- OpenCV preprocessing.
- Structured Markdown + JSON canonical documents.
- BGE-small-en-v1.5 for embeddings.
- Qdrant for dense vector storage.
- Phi-4-mini-instruct Q4 behind a llama.cpp-compatible server.
- SQLite and local filesystem for manifests, jobs, sessions, and artifacts.
- A lightweight durable worker with conservative CPU/RAM concurrency.
- Docker Compose with only the API published on loopback.

Model identifiers, artifact paths, local service URLs, thresholds, languages, context budgets, and concurrency are environment-overridable. Overrides remain subject to the local-only network policy: they may select another local model/runtime, but must not silently enable a hosted API.

## 2. Scope boundaries and decisions

### In scope

- Real local embedding and generation services, replacing deterministic development behavior.
- Selective, structured parsing for PDF, PNG/JPEG, TIFF, DOCX, TXT, Markdown, and existing JSON/JSONL corpora.
- Page/section/element/bounding-box provenance suitable for citations and tables.
- Durable, restart-safe ingestion jobs and resource backpressure.
- Qdrant as the default dense vector backend, with a versioned full reindex.
- Local evaluation and an offline smoke/evaluation corpus.
- Offline model artifact acquisition, pinning, verification, and runtime packaging.
- Fail-closed egress and loopback-only default exposure.

### Not in the default runtime

- OpenAI, Gemini, Tavily, or any other hosted inference/search provider.
- Runtime model downloads, telemetry, update checks, or corpus downloads.
- PostgreSQL/pgvector, Redis, nginx, and Dozzle unless retained in explicitly non-default local profiles for a demonstrated need.
- Multi-tenant or internet-facing deployment.
- A desktop UI; the existing REST API is sufficient for this pivot.

### Key implementation decisions

1. Keep the modular monolith and current provider interfaces; add/replace adapters rather than rewrite the agent or retrieval layers.
2. Rename deterministic `local` providers to `test` or `deterministic`. The name `local` must mean real local inference.
3. Use a local OpenAI-compatible HTTP contract for generation and a small explicit embedding HTTP contract. Base URLs are configurable and host-allowlisted.
4. Use SQLite WAL plus atomic job leases for the first durable queue. This removes the need for Redis on the target laptop and keeps recovery inspectable.
5. Run parsing/OCR/embedding in `document-worker`; keep answer generation request-driven but guarded by a process-wide semaphore. Defaults are OCR concurrency 1, one ingestion job, bounded embedding batches, and generation concurrency 1.
6. Treat extracted structure as durable source data. Flat chunk text is derived data that can be rebuilt.
7. Replace indexes by version; do not mutate the existing 32-dimensional collection in place.
8. Put runtime services on an internal Docker network. Publish only the API to `127.0.0.1`.

## 3. Target runtime topology

```text
Local client
    |
    | http://127.0.0.1:${CITEBOT_PORT}
    v
rag-api ------------------------------+
    |                                 |
    | SQLite jobs/sessions/manifests  | generation semaphore=1
    |                                 v
    +----> document-worker          llm-server
    |          |                     Phi-4-mini Q4
    |          +--> native parser
    |          +--> PaddleOCR/PP-StructureV3
    |          +--> Tesseract fallback
    |          +--> embedding-server
    |                    BGE-small-en-v1.5
    v
  qdrant

All service-to-service traffic: private Docker `internal: true` network.
Only `rag-api`: loopback host port.
All model and application state: named volumes or explicit local bind mounts.
```

## 4. Environment contract

The implementation should use one authoritative `.env.example`, expose effective non-secret configuration in the version/readiness response, and validate the complete contract at startup.

The checked-in `.env.example` and Compose deployment set `RUNTIME_MODE=offline`.
Direct `Settings()` construction retains a development-compatible default so the
legacy deterministic test fixtures and compatibility adapters remain usable without
an environment file; production deployments must set the offline value explicitly.

| Variable | Default | Override behavior / validation |
| --- | --- | --- |
| `RUNTIME_MODE` | `offline` | Only `offline` is valid in the shipped runtime image. Test suites may use `test` explicitly. |
| `LOCAL_SERVICE_HOSTS` | `embedding,llm,qdrant,ocr-worker,localhost,127.0.0.1,::1` | Comma-separated allowlist for configured HTTP service hosts. |
| `API_BIND_HOST` | `127.0.0.1` | Non-loopback values require an explicit local-LAN profile and admin/research keys. |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/citebot.db` | May point to a user-selected local SQLite file; a local PostgreSQL override is optional, not default. |
| `SQLITE_BUSY_TIMEOUT_MS` | `5000` | Validate positive bounded value; enable WAL during initialization. |
| `OBJECT_STORAGE_PATH` | `/data/documents` | Must be writable and must not escape the configured data root in container mode. |
| `STRUCTURED_DOCUMENT_PATH` | `/data/structured` | Stores versioned canonical JSON/Markdown and page artifacts. |
| `SPARSE_INDEX_PATH` | `/data/sparse_index.sqlite3` | SQLite FTS5 sparse index with transactional updates; legacy JSON is migrated to a sibling SQLite file. |
| `VECTOR_BACKEND` | `qdrant` | Allow `qdrant` or `local` for tests/small fallback. Do not dual-write by default. |
| `QDRANT_URL` | `http://qdrant:6333` | Host must pass the local-service allowlist. |
| `QDRANT_COLLECTION` | `citebot_chunks_bge_small_v1` | Versioned; never reuse an incompatible-dimension collection. |
| `ALLOW_LOCAL_DENSE_FALLBACK` | `false` in Compose | Prevents an outage from triggering full-corpus re-embedding; enable only for tests/small corpora. |
| `MAX_LOCAL_DENSE_CANDIDATES` | `512` | Hard cap for explicit local dense fallback. |
| `DOCUMENT_PARSER` | `auto` | `auto`, `native`, or `ocr`; `auto` is the production default. |
| `OCR_PROVIDER` | `paddleocr` | Local adapter identifier; model/data paths configured separately. |
| `OCR_FALLBACK_PROVIDER` | `tesseract` | Allow `none` for constrained installs. |
| `OCR_LANGUAGES` | `en` | Comma-separated installed language packs; fail readiness if requested packs are absent. |
| `OCR_MIN_NATIVE_TEXT_COVERAGE` | `0.60` | Page-level threshold; benchmark and tune against representative fixtures. |
| `OCR_MIN_CONFIDENCE` | `0.75` | Below this, retry/fallback or flag the element/page as uncertain. |
| `OCR_MAX_PAGES` | `100` | Conservative CPU/RAM input limit for the 16 GB profile; raise only after a soak benchmark. |
| `OCR_CONCURRENCY` | `1` | Reject values above detected/configured resource budget unless a force flag is used. |
| `EMBEDDING_PROVIDER` | `local-http` | `test` is permitted only in test mode; hosted providers are invalid. |
| `EMBEDDING_BASE_URL` | `http://embedding:8081` | Host must pass the allowlist. |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Local artifact/model identifier; override requires a new embedding/index version. |
| `EMBEDDING_DIMENSION` | `384` | Validate against service metadata before opening/creating a collection. |
| `EMBEDDING_BATCH_SIZE` | `4` | Tune from benchmark; worker must cap payload bytes as well as item count. |
| `EMBEDDING_VERSION` | `bge-small-en-v1.5` | Required in chunks and vector payloads. |
| `ANSWER_PROVIDER` | `llama-cpp` | `test` only in test mode; hosted providers are invalid. |
| `LLM_BASE_URL` | `http://llm:8082/v1` | Host must pass the allowlist. |
| `ANSWER_MODEL` | `phi-4-mini-instruct-q4` | Maps to a pinned local GGUF artifact. |
| `LLM_CONTEXT_TOKENS` | `4096` | Conservative CPU/RAM default; 8192 is an opt-in profile after measured peak-RSS validation. |
| `LLM_GENERATION_CONCURRENCY` | `1` | Enforced in the model adapter; research admission also bounds active and waiting requests. |
| `RESEARCH_CONCURRENCY` / `RESEARCH_QUEUE_SIZE` | `1` / `2` | Bound expensive graph executions on a CPU-only host. |
| `ENABLE_RERANKING` | `true` | Keep current interface. |
| `RERANKER_PROVIDER` | `heuristic` | Default avoids another resident model; allow a pinned local cross-encoder after benchmarks. |
| `ALLOW_WEB_SEARCH_DEFAULT` | `false` | Must remain false in offline runtime; web tool is not constructed. |
| `ALLOW_PYTHON_EXECUTION_DEFAULT` | `false` | Preserve opt-in local sandbox; unrelated to network access. |
| `EVALUATION_EVALUATOR_PROVIDER` | `local` | Local deterministic metrics by default; optional local-LLM judge after calibration. |
| `MODEL_MANIFEST_PATH` | `/models/manifest.lock.json` | Startup verifies file presence, size, checksum, and declared license metadata. |

The `384` embedding dimension must be verified against the chosen local artifact during Phase 2. Startup must compare runtime model metadata with `EMBEDDING_DIMENSION` and the Qdrant collection schema, then fail before ingestion/query if they disagree.

## 5. Data model and API changes

### Canonical structured document

Add versioned Pydantic/domain schemas for:

- `StructuredDocument`: document ID, source URI/name, media type, content hash, parser version, language, page count, metadata, and pages.
- `StructuredPage`: one-based page number, dimensions, rotation, extraction method, native-text coverage, OCR status, and ordered elements.
- `DocumentElement`: stable element ID, type (`heading`, `paragraph`, `list`, `table`, `caption`, `image_text`, etc.), text and/or Markdown, bounding box, reading order, section path, extraction confidence, and source engine.
- `ExtractionIssue`: page/element scope, code, severity, engine attempts, and human-readable detail.

Persist canonical JSON and Markdown in `STRUCTURED_DOCUMENT_PATH`. Extend SQL metadata enough to query and audit extraction without putting the full page tree in relational rows. Extend chunk metadata/vector payloads with `element_ids`, `page_start`, `page_end`, `bbox_refs`, `extraction_method`, `min_confidence`, `canonical_version`, and table-aware location markers.

### Job model

Evolve `ingestion_jobs` into a durable queue with:

- `queued_at`, `available_at`, `lease_owner`, `lease_expires_at`, `attempt_count`, `max_attempts`, `heartbeat_at`, `stage`, `progress_current`, `progress_total`, and structured error JSON.
- Atomic claim/update operations compatible with SQLite transactions.
- Idempotency key over content hash + parser version + embedding version + index version.
- Stale-lease recovery on worker startup.
- Explicit terminal states: `completed`, `failed`, `cancelled`, `quarantined`.

### API behavior

- Change ingestion submission to enqueue and return `202 Accepted` with job status URL.
- Preserve current status endpoint and add stage/progress/extraction-issue fields without breaking existing core fields.
- Add a local file-import contract first. If byte uploads are added, stream to a quarantine directory and enforce total bytes, page count, MIME/signature checks, filename neutralization, decompression limits, and cleanup.
- Extend search and citation responses with page/element/bounding-box references while preserving current text/location fields during migration.
- Readiness must report SQLite, storage paths, Qdrant, embedding service/model, LLM service/model, OCR artifacts, and worker heartbeat independently.
- Version endpoint must report runtime mode, canonical/index/model versions, and redacted local endpoints.

## 6. Phased execution plan

### Phase 0 — Freeze the contract and protect current behavior

Goal: create a safe migration seam before changing providers or data.

Tasks:

1. Add architecture decision records for offline policy, local service contracts, structured canonical documents, SQLite queue, and versioned reindexing.
2. Capture current unit/integration/evaluation baselines and export existing SQLite/Qdrant/pgvector data if it must be retained.
3. Add `RUNTIME_MODE`, local host allowlisting, and model/index version settings in `app/core/config.py`.
4. Make configuration reject OpenAI/Gemini/Tavily providers and public service URLs in offline mode.
5. Rename deterministic providers to `test` in code and fixtures; reject them outside `APP_ENV=test` or `RUNTIME_MODE=test`.
6. Change evaluation defaults from OpenAI to local metrics.

Primary files: `app/core/config.py`, `app/core/lifecycle.py`, `app/agents/generation.py`, `app/ingestion/embedder.py`, `app/tools/web_search.py`, `app/evaluation/`, `.env.example`, tests and architecture docs.

Exit criteria:

- Existing tests pass under explicit test providers.
- A production/offline configuration containing a hosted provider or non-allowlisted URL fails at startup with a precise error.
- No network tool/generator/embedder is constructed in offline mode.

### Phase 1 — Local model service adapters and health

Goal: deliver a real local text-only RAG vertical slice before adding OCR.

Tasks:

1. Implement `LocalHttpEmbedder` with batch limits, response-dimension validation, timeouts, cancellation, and typed failure mapping.
2. Implement `LlamaCppAnswerGenerator` against the local OpenAI-compatible endpoint, retaining the current citation-constrained response schema.
3. Add service metadata probes that confirm model identity, dimension/context capacity, and artifact version.
4. Add generation semaphore/queue timeout and return a clear local-overload response rather than spawning concurrent CPU-heavy generations.
5. Add `embedding-server` and `llm-server` Compose services with pinned image digests and health checks.
6. Keep deterministic providers only in unit tests; add contract tests using stub local HTTP servers and one opt-in real-model smoke test.

Primary files: `app/ingestion/embedder.py`, `app/agents/generation.py`, `app/core/health.py`, `app/core/lifecycle.py`, `docker-compose.yml`, model service configs, tests.

Exit criteria:

- Text/JSON ingestion, retrieval, answer generation, and citation verification succeed with network egress disabled.
- Runtime reports the expected BGE and Phi model/artifact versions.
- Two concurrent answer requests never cause more than one active model generation at the default setting.

### Phase 2 — Structured parsing and selective OCR

Goal: preserve document structure and use OCR only where native extraction is inadequate.

Tasks:

1. Add format detection based on file signatures plus bounded MIME validation; do not trust extensions alone.
2. Implement native adapters for PyMuPDF, DOCX, images/TIFF, TXT/Markdown, and existing JSON/JSONL inputs.
3. Implement page-level native-text quality scoring using text coverage, printable-character ratio, replacement/control characters, and suspiciously empty image-heavy pages.
4. Render only pages that fail the native-text gate; preprocess with orientation detection, deskew, rotation, denoise, and bounded resolution.
5. Add PaddleOCR/PP-StructureV3 adapter producing ordered structured elements and table Markdown/JSON.
6. Retry low-confidence regions/pages with Tesseract, keep both attempt records, select the best result by explicit rules, and flag unresolved uncertainty.
7. Persist canonical JSON/Markdown atomically and update structure-aware chunking so headings, tables, and page boundaries are not split blindly.
8. Extend Qdrant payloads/search results/citations with structured provenance.
9. Build fixtures for digital, scanned, mixed, rotated, multi-column, table-heavy, poor-quality, malformed, and oversized documents.

Primary files: new `app/documents/` or `app/parsing/` package, `app/ingestion/loaders.py`, `normalizer.py`, `chunker.py`, `schemas.py`, `object_store.py`, `app/db/models.py`, migrations, retrieval/citation schemas, tests.

Exit criteria:

- Digital PDFs above the quality threshold do not invoke OCR.
- Mixed PDFs OCR only failing pages.
- Tables/headings/page coordinates survive canonicalization, chunking, indexing, retrieval, and citation responses.
- Malformed/oversized inputs fail safely without leaving partial canonical or vector state.

### Phase 3 — Durable worker and resource governance

Goal: isolate heavy ingestion work and keep a 16 GB CPU host responsive.

Tasks:

1. Add SQLite WAL initialization, busy timeout, queue repository, atomic leases, heartbeat, retry policy, cancellation, and stale-job recovery.
2. Move ingestion execution out of the request process into a `document-worker` command/service; submission only validates/enqueues.
3. Enforce one active ingestion job and OCR concurrency 1 by default; batch embedding with both item and byte limits.
4. Add per-stage timeouts, subprocess termination, temporary-file quotas, disk free-space checks, and cleanup on cancellation/failure.
5. Emit job wait/stage duration, OCR fallback rate, page throughput, embedding throughput, model queue wait, RSS/CPU warnings, and failure counters without logging document content.
6. Remove Redis from the default Compose path unless testing proves SQLite leasing insufficient for the single-host target.

Primary files: `app/db/models.py`, migrations, new queue repository/worker CLI, `app/ingestion/service.py`, admin routes, metrics, Compose, tests.

Exit criteria:

- API restarts do not lose queued jobs; worker restarts recover expired leases and do not duplicate committed chunks.
- OCR, embedding, and generation concurrency never exceeds configured limits.
- API health remains responsive during a worst-case OCR fixture on the target laptop.

### Phase 4 — Versioned BGE/Qdrant cutover

Goal: migrate from deterministic 32-dimensional vectors to the real default embedding index without an unsafe in-place change.

Tasks:

1. Add a model/collection compatibility check before reads or writes.
2. Create `citebot_chunks_bge_small_v1` (or a generated versioned name) with the verified BGE dimension and payload indexes.
3. Rebuild chunks from canonical structured documents and re-embed into the new collection using resumable jobs.
4. Run retrieval/evaluation comparisons against the old and new indexes; do not blend scores across embedding spaces.
5. Switch the active collection through one config/alias change after gates pass.
6. Retain the old collection and data export for a defined rollback window; document cleanup as a separate explicit operation.

Primary files: vector writers/retrievers, ingestion reindex flow, config, evaluation datasets/harness, release docs.

Exit criteria:

- Collection/model/dimension mismatch fails readiness.
- Reindex resumes after interruption and produces deterministic document/chunk counts.
- Local retrieval and citation thresholds meet the quality gates in Section 8.
- Rollback to the prior collection is documented and tested before deletion is considered.

### Phase 5 — Offline packaging and deployment hardening

Goal: make offline behavior a deployment property, not merely a provider convention.

Tasks:

1. Create a pinned model manifest containing artifact name, upstream identifier, revision, filename, byte size, SHA-256, license metadata, and runtime compatibility.
2. Provide a deliberate networked build/bootstrap command that fetches artifacts before deployment; never download on application startup.
3. Build/release images or an offline bundle containing all Python wheels, system packages, OCR data, and model artifacts required at runtime.
4. Configure an internal Compose network; remove host ports from Qdrant, embedding, LLM, and OCR/worker services.
5. Bind the API port as `127.0.0.1:${CITEBOT_PORT:-8000}:8000`; remove nginx/Dozzle from the default profile.
6. Run containers as non-root, use read-only roots where practical, mount only explicit writable data/model/temp paths, set resource limits, and add `no-new-privileges`/capability restrictions.
7. Separate corpus-download scripts into a non-runtime tooling image/profile and prove that the runtime starts with DNS/internet unavailable.
8. Add backup/restore and data-deletion commands for SQLite, structured/raw documents, sparse indexes, Qdrant, evaluation artifacts, and logs.

Primary files: `Dockerfile`, `docker-compose.yml`, model manifest/build scripts, `.dockerignore`, release/security docs, CI.

Exit criteria:

- A clean offline host can install/start from the prepared bundle with no network request.
- An egress attempt from every runtime service fails, while internal service calls succeed.
- Only the API is host-published, and it is bound to loopback by default.
- Model/artifact checksum failure blocks readiness and inference.

### Phase 6 — Quality, performance, cutover, and cleanup

Goal: make the new stack the documented and tested default, then retire contradictory paths.

Tasks:

1. Expand evaluation data with representative private/local document shapes using redistributable or synthetic fixtures.
2. Benchmark cold start, warm query, OCR pages/minute, embedding chunks/minute, peak RSS, queue wait, and answer latency on the target 16 GB machine.
3. Tune context, batch, OCR threshold, and timeouts from measured results; record hardware with every benchmark.
4. Add privacy regression tests that fail on hosted domains, telemetry SDKs, runtime downloads, unexpected DNS, content-bearing logs, and non-loopback port mappings.
5. Update README, system design, security model, requirements, runbooks, and troubleshooting to describe only the supported default.
6. Remove deprecated hosted-provider code and unused dependencies after one migration window; do not leave environment variables that imply cloud support.
7. Tag the last pre-pivot release and publish the data/index migration and rollback instructions.

Exit criteria:

- All Section 8 gates pass on the target hardware and in CI where applicable.
- Documentation and `.env.example` contain no hosted-provider default or ambiguous `local` test-double behavior.
- A repository scan finds no deployed-runtime URL or SDK path to hosted inference/search services.

## 7. Test plan

### Unit and contract tests

- Configuration matrix: offline/test modes, host allowlist, invalid public URLs, model/dimension mismatch, test-provider restrictions.
- Parser decision logic: native coverage, mixed pages, OCR retry/fallback, reading order, tables, coordinates, confidence propagation.
- Queue semantics: atomic claim, lease expiry, retry, cancellation, idempotency, crash recovery, duplicate submission.
- Local HTTP adapters: schema validation, timeouts, cancellation, overload, malformed responses, wrong dimensions/models.
- Chunk/citation provenance: stable element IDs and exact page/bounding-box references.
- Log redaction and error responses: no document body, prompt, API secret, or local absolute host path leakage.

### Integration tests

- Compose text-only RAG slice with local models and Qdrant.
- Digital PDF path with zero OCR calls.
- Mixed/scanned document path through PaddleOCR and forced Tesseract fallback.
- Worker/API restart during each ingestion stage.
- Versioned reindex and collection rollback.
- Runtime on an internal network with external DNS/HTTP unavailable.
- Backup, restore, and explicit document deletion across every state store.

### Manual target-host tests

- 16 GB host preflight and model compatibility.
- Peak RSS and swap behavior under simultaneous queued ingestion and query load.
- Thermal throttling/long OCR run and cancellation.
- Cold/warm latency and usability at 4K, 8K, and 12K contexts before freezing the default.

## 8. Acceptance gates

### Privacy and offline

- Zero outbound DNS/HTTP during boot, readiness, ingestion, retrieval, reranking, generation, evaluation, and shutdown.
- Only the API port is published, on `127.0.0.1` by default.
- No document/prompt text in logs, metrics labels, health responses, or exception traces.
- Model and OCR artifacts are pinned and checksum-verified.

### Correctness and retrieval quality

- Citation support rate remains `1.0` on the existing quality baseline.
- Context precision and answer relevance remain at or above current repository thresholds (`0.5` each), with new scanned/table fixture thresholds recorded separately.
- Every answer citation resolves to a stored chunk and canonical page/element provenance.
- Table questions retrieve the relevant table element without flattening away row/column meaning.

### Reliability

- No lost queued jobs across API/worker restart.
- Repeated submission with the same idempotency key creates no duplicate committed chunks or vector points.
- Partial OCR/embed/vector failures are retryable or quarantined with inspectable state.
- Disk-full, model-unavailable, corrupt-file, and Qdrant-unavailable scenarios fail without corrupting canonical data.

### Resource budget

- Default configuration runs within 16 GB physical RAM without sustained swap thrashing on the agreed representative workload.
- OCR and LLM concurrency are 1; embedding batch size is bounded and benchmarked.
- Health/status endpoints remain responsive during ingestion.
- Measured latency and throughput are recorded with hardware, model hash, context, and document mix; numeric SLOs are set from the first target-host benchmark rather than guessed.

## 9. Migration and rollback

1. Tag/export the current release and inventory all SQLite/PostgreSQL, filesystem, sparse-index, pgvector, and Qdrant state.
2. Deploy the new configuration contract and local model services without deleting old providers or indexes.
3. Write canonical structured documents alongside existing raw text; do not overwrite source files.
4. Build the new versioned Qdrant collection from canonical data.
5. Run retrieval and answer evaluations against both configurations.
6. Switch the active collection/model versions in one environment change and restart/readiness-check the stack.
7. During the rollback window, revert the environment to the prior application/index version; never mix old vectors with the new embedder.
8. Remove old hosted adapters and old indexes only after explicit acceptance and a verified backup. Cleanup is intentionally separate from migration.

Schema migrations should be additive until cutover completes. New API fields should be additive, and old flat citation fields should remain populated from structured provenance for one compatibility window.

## 10. Implementation sequence and dependency map

```text
Phase 0 config/offline contract
          |
          v
Phase 1 real local text-only RAG
          |
          +-------------------+
          v                   v
Phase 2 structured OCR     Phase 3 durable worker/backpressure
          |                   |
          +---------+---------+
                    v
          Phase 4 versioned reindex
                    |
                    v
          Phase 5 offline packaging
                    |
                    v
          Phase 6 cutover/cleanup
```

Phases 2 and 3 may be developed in parallel only after the canonical-document and job contracts are frozen. Phase 4 depends on both because it must rebuild from the final canonical representation through the durable worker. Hosted-provider deletion belongs late in Phase 6 so rollback remains possible, while offline-mode validation and network isolation land at the beginning and are never optional.

## 11. First implementation slice

The first pull request should be deliberately narrow:

1. Add `RUNTIME_MODE=offline`, service-host allowlisting, and startup validation.
2. Rename deterministic providers to `test` and update fixtures.
3. Add local embedding and llama.cpp adapter interfaces with stub HTTP contract tests, without downloading models in CI.
4. Set the environment/documentation defaults from Section 4.
5. Disable construction of hosted/web tools in offline mode.
6. Add readiness placeholders for model identity/dimension checks.

This slice proves the most important semantic change—`local` means real local inference and offline mode fails closed—before the repository takes on OCR, queue, schema, and reindex complexity.
