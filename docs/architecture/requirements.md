# Requirements

## Scope

- Provide a FastAPI service with health, readiness, version, and admin ingestion endpoints.
- Provide an integrated responsive workspace for browser uploads, document status, durable conversations, streaming answers, and citation inspection.
- Support an offline Docker Compose deployment with PostgreSQL/pgvector, local model services, and a document worker.
- Persist canonical document metadata, chunk metadata, and ingestion job state.
- Preserve source URIs, offsets, and location markers required for citation traceability.
- Preserve page/element/bounding-box provenance and extraction confidence for OCR-backed citations.

## Non-Functional Baseline

- Development startup must work with one command through `make dev-up`.
- Production settings must fail fast when incompatible values are supplied.
- Readiness must validate PostgreSQL, local embedding, and local generation services when enabled.
- Offline runtime must not perform outbound DNS/HTTP or runtime model downloads.
- Ingestion must support restart-safe queued jobs and selective OCR for scanned pages.
- Re-ingestion of unchanged documents must skip duplicate work.
