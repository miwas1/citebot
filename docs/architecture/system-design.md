# System Design

## Current Foundation

- FastAPI application bootstrap with dependency-aware lifespan initialization.
- Async SQLAlchemy metadata store for documents, chunks, and ingestion jobs.
- Local object-store abstraction backed by the filesystem.
- Pluggable embedding pipeline with deterministic local embeddings and HTTP-ready OpenAI expansion point.
- pgvector, Qdrant, and sparse index writers behind explicit ingestion orchestration.
- Dense retrieval service with backend routing across pgvector, Qdrant, and a local fallback path.
- Hybrid retrieval pipeline that fuses dense and sparse candidates, deduplicates chunk IDs, and applies reranking.
- LangGraph-backed research agent that performs validation, query classification, retrieval planning, hybrid retrieval, optional Tavily web search, optional sandboxed Python analysis, answer generation, and citation verification.
- In-memory session persistence with compressed conversation memory, citation graph retention, trace IDs, and replayable state transitions for lower-environment debugging.

## Phase 2 Ingestion Flow

1. Load supported corpus files from disk.
2. Normalize text and compute a stable content hash.
3. Skip unchanged documents unless `force_reindex` is requested.
4. Chunk normalized text with overlap and stable chunk identifiers.
5. Generate embeddings and write metadata plus index payloads.
6. Persist job counters for observability and replay.

## Retrieval Flow

1. Embed the query through the configured embedding provider.
2. Route dense retrieval to Qdrant or pgvector, with local fallback when remote stores are unavailable.
3. Run sparse BM25-style retrieval over the persisted sparse index.
4. Normalize dense and sparse scores, apply weighted reciprocal-rank fusion, and remove duplicate chunks.
5. Rerank the fused candidate window before returning explainable results to the caller.

## Research Agent Flow

1. Validate and normalize the user query.
2. Classify freshness and computation signals, then build a retrieval/tool plan.
3. Run hybrid retrieval over the ingested corpus.
4. Escalate to Tavily only when policy allows and internal context is weak or freshness is required.
5. Run sandboxed Python analysis only when the request explicitly allows it and supplies code.
6. Generate a structured answer with citations from the accumulated contexts.
7. Verify every citation against retrieved support and return a guarded answer when support is incomplete.
8. Persist recent turns plus compressed memory for follow-up questions.
