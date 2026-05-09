# System Design

## Current Foundation

- FastAPI application bootstrap with dependency-aware lifespan initialization.
- Async SQLAlchemy metadata store for documents, chunks, and ingestion jobs.
- Local object-store abstraction backed by the filesystem.
- Pluggable embedding pipeline with deterministic local embeddings and HTTP-ready OpenAI expansion point.
- pgvector, Qdrant, and sparse index writers behind explicit ingestion orchestration.

## Phase 2 Ingestion Flow

1. Load supported corpus files from disk.
2. Normalize text and compute a stable content hash.
3. Skip unchanged documents unless `force_reindex` is requested.
4. Chunk normalized text with overlap and stable chunk identifiers.
5. Generate embeddings and write metadata plus index payloads.
6. Persist job counters for observability and replay.
