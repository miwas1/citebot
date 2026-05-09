# Requirements

## Scope

- Provide a FastAPI service with health, readiness, version, and admin ingestion endpoints.
- Support local development with Docker Compose for PostgreSQL with pgvector, Qdrant, and Redis.
- Persist canonical document metadata, chunk metadata, and ingestion job state.
- Preserve source URIs, offsets, and location markers required for citation traceability.

## Non-Functional Baseline

- Development startup must work with one command through `make dev-up`.
- Production settings must fail fast when incompatible values are supplied.
- Readiness must validate database connectivity and, when enabled, Qdrant connectivity.
- Re-ingestion of unchanged documents must skip duplicate work.
