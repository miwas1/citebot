# Citation Traceability

CiteBot stores every chunk with a source document identifier, a stable chunk identifier, and character offsets.
This makes follow-up retrieval and citation verification possible without inventing sources.

## Ingestion

The ingestion pipeline normalizes text, computes a content hash, skips unchanged documents, and writes chunk metadata to the primary database.
