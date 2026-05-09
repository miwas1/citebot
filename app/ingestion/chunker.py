"""Chunking utilities tuned for citation-friendly overlapping spans."""

from __future__ import annotations

import re
from uuid import NAMESPACE_URL, uuid5

from app.ingestion.schemas import CanonicalDocument, ChunkPayload


class SlidingWindowChunker:
    """Create overlapping chunks from a normalized document."""

    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        """Configure the token window size and overlap budget."""

        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def chunk(
        self,
        document: CanonicalDocument,
        embedding_model: str,
        embedding_version: str,
        index_version: str,
    ) -> list[ChunkPayload]:
        """Split a document into stable, overlapping chunk records."""

        matches = list(re.finditer(r"\S+", document.text))
        if not matches:
            return []

        step = self._chunk_size - self._chunk_overlap
        chunks: list[ChunkPayload] = []
        for start_index in range(0, len(matches), step):
            window = matches[start_index : start_index + self._chunk_size]
            if not window:
                break
            char_start = window[0].start()
            char_end = window[-1].end()
            chunk_text = document.text[char_start:char_end].strip()
            if not chunk_text:
                continue
            section = self._find_section_heading(document.text, char_start)
            chunk_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{document.document_id}:{char_start}:{char_end}:{index_version}",
                )
            )
            chunks.append(
                ChunkPayload(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    source_uri=document.source_uri,
                    title=document.title,
                    text=chunk_text,
                    token_count=len(window),
                    char_start=char_start,
                    char_end=char_end,
                    section=section,
                    page=None,
                    location_marker=f"chars {char_start}-{char_end}",
                    embedding_model=embedding_model,
                    embedding_version=embedding_version,
                    index_version=index_version,
                )
            )
            if start_index + self._chunk_size >= len(matches):
                break
        return chunks

    def _find_section_heading(self, text: str, char_offset: int) -> str | None:
        """Return the most recent Markdown heading that precedes a chunk."""

        heading = None
        for match in re.finditer(r"(?m)^#{1,6}\s+(.+)$", text):
            if match.start() > char_offset:
                break
            heading = match.group(1).strip()
        return heading
