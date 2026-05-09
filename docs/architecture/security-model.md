# Security Model

## Current Controls

- Production configuration rejects invalid pgvector or embedding-provider combinations early.
- Admin ingestion is isolated under `/api/v1/admin/ingestion` for future auth layering.
- Raw documents are persisted to an explicit storage path instead of transient temp files.
- External Qdrant access is opt-in through `ENABLE_QDRANT`.

## Follow-On Work

- Add authentication and authorization for admin routes.
- Move local filesystem storage to managed object storage per environment.
- Add secret management for OpenAI and tool credentials.
