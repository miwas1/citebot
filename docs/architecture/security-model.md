# Security Model

## Current Controls

- Production configuration rejects invalid pgvector or embedding-provider combinations early.
- Research, admin ingestion, evaluation, metrics, and readiness routes accept bearer or `X-API-Key` credentials when their configured scope key is present.
- Production configuration requires both `RESEARCH_API_KEY` and `ADMIN_API_KEY` so an accidentally unset key cannot expose protected routes.
- Raw documents are persisted to an explicit storage path instead of transient temp files.
- Offline runtime validates model/vector URLs against a local-host allowlist and fails closed for hosted providers.
- Compose publishes only the API on loopback; model and vector services use an internal network.
- Model artifacts are expected to be pinned and checksum-verified before runtime startup.
- The Python analysis tool is bounded by AST validation, subprocess time, memory, and output limits; it is not a hostile multi-tenant security boundary.

## Follow-On Work

- Add artifact-manifest verification and model-service readiness checks to release automation.
- Keep hosted credentials and corpus-download tools outside the deployed runtime image.
- Add a stronger isolated execution boundary before enabling Python execution for untrusted users.
