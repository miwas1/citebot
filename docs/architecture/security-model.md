# Security Model

## Current Controls

- Production configuration rejects invalid pgvector or embedding-provider combinations early.
- Research, admin ingestion, evaluation, metrics, and readiness routes accept bearer or `X-API-Key` credentials when their configured scope key is present.
- Production configuration requires both `RESEARCH_API_KEY` and `ADMIN_API_KEY` so an accidentally unset key cannot expose protected routes.
- Raw documents are persisted to an explicit storage path instead of transient temp files.
- External Qdrant access is opt-in through `ENABLE_QDRANT`.
- The Python analysis tool is bounded by AST validation, subprocess time, memory, and output limits; it is not a hostile multi-tenant security boundary.

## Follow-On Work

- Move local filesystem storage to managed object storage per environment.
- Add secret management for OpenAI and tool credentials.
- Add a stronger isolated execution boundary before enabling Python execution for untrusted users.
