# Quality Thresholds

## Phase 1 and 2 Baseline

- `readiness` must return success when the configured database is reachable.
- Every chunk must include `document_id`, `chunk_id`, `char_start`, `char_end`, and `location_marker`.
- Ingestion jobs must record `documents_seen`, `documents_indexed`, `documents_skipped`, and `chunks_written`.
- Duplicate ingestion without `force_reindex` must increase skipped counts instead of rewriting data.
