# System Design

## Current Foundation

- FastAPI application bootstrap with dependency-aware lifespan initialization.
- Async SQLAlchemy metadata store for documents, chunks, and ingestion jobs.
- Local object-store abstraction backed by the filesystem, with structured JSON artifacts.
- Pluggable embedding pipeline with a local OpenAI-compatible HTTP adapter and deterministic test provider.
- Qdrant and sparse index writers behind explicit ingestion orchestration; pgvector remains an optional compatibility profile.
- PyMuPDF native extraction followed by selective PaddleOCR/PP-StructureV3 and Tesseract fallback.
- SQLite-backed durable ingestion queue with a separate worker and bounded local-model concurrency.
- Dense retrieval service with backend routing across pgvector, Qdrant, and a local fallback path.
- Hybrid retrieval pipeline that fuses dense and sparse candidates, deduplicates chunk IDs, and applies reranking.
- LangGraph-backed research agent that performs validation, query classification, retrieval planning, hybrid retrieval, optional policy-gated web search, optional sandboxed Python analysis, local answer generation, and citation verification.
- SQLite-backed session persistence with compressed conversation memory, citation graph retention, trace IDs, and replayable state transitions for lower-environment debugging.

## Phase 2 Ingestion Flow

1. Validate local file signatures and load supported corpus files from disk.
2. Extract native PDF text and assess page quality; OCR only pages below the configured gate.
3. Preserve document -> page -> element -> coordinates structure as versioned JSON/Markdown.
4. Normalize text and compute a stable content hash.
3. Skip unchanged documents unless `force_reindex` is requested.
4. Chunk normalized text with overlap and stable chunk identifiers.
6. Generate local embeddings and write metadata plus Qdrant payloads.
7. Persist job counters, leases, and structured extraction issues for observability and replay.

## Retrieval Flow

1. Embed the query through the configured embedding provider.
2. Route dense retrieval to the versioned Qdrant collection, with a local fallback for tests/small installs.
3. Run sparse BM25-style retrieval over the persisted sparse index.
4. Normalize dense and sparse scores, apply weighted reciprocal-rank fusion, and remove duplicate chunks.
5. Rerank the fused candidate window before returning explainable results to the caller.

## Research Agent Flow

1. Validate and normalize the user query.
2. Classify freshness and computation signals, then build a retrieval/tool plan.
3. Run hybrid retrieval over the ingested corpus.
4. Keep web search disabled in offline mode; a non-default compatibility profile may add it explicitly.
5. Run sandboxed Python analysis only when the request explicitly allows it and supplies code.
6. Generate a structured answer with citations from the accumulated contexts.
7. Verify every citation against retrieved support and return a guarded answer when support is incomplete.
8. Persist recent turns plus compressed memory for follow-up questions.
