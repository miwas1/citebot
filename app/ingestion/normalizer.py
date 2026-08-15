"""Document normalization and stable identifier generation."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from app.ingestion.schemas import CanonicalDocument, LoadedDocument


class DocumentNormalizer:
    """Normalize raw documents into canonical ingestion-ready records."""

    def normalize(self, document: LoadedDocument) -> CanonicalDocument:
        """Clean the text payload and compute a stable content hash and document identifier."""

        normalized_text = self._normalize_text(document.text)
        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        structured = self._align_structured(document.structured, normalized_text)
        if structured is not None:
            structured.document_id = str(uuid5(NAMESPACE_URL, document.source_uri))
            structured.source_content_hash = content_hash
        return CanonicalDocument(
            document_id=str(uuid5(NAMESPACE_URL, document.source_uri)),
            source_uri=document.source_uri,
            title=document.title.strip() or document.source_uri,
            text=normalized_text,
            publisher=document.publisher,
            published_at=document.published_at,
            ingested_at=datetime.now(tz=UTC),
            content_hash=content_hash,
            access_policy=document.access_policy,
            metadata=document.metadata,
            structured=structured,
        )

    def _normalize_text(self, text: str) -> str:
        """Collapse whitespace, strip null bytes, and preserve paragraphs."""

        # PostgreSQL/asyncpg cannot store null bytes (\0x00) in VARCHAR/TEXT columns
        text = text.replace("\x00", "")

        cleaned_lines = [
            re.sub(r"\s+", " ", line).strip() for line in text.splitlines()
        ]
        non_empty_lines = [line for line in cleaned_lines if line]
        return "\n\n".join(non_empty_lines).strip()

    def _align_structured(
        self,
        structured,
        normalized_text: str,
    ):
        """Normalize element text and recompute offsets against canonical text."""

        if structured is None:
            return None
        cursor = 0
        pages = structured.model_copy(deep=True)
        for page in pages.pages:
            for element in page.elements:
                element.text = self._normalize_text(element.text)
                if not element.text:
                    continue
                start = normalized_text.find(element.text, cursor)
                if start < 0:
                    continue
                element.char_start = start
                element.char_end = start + len(element.text)
                cursor = element.char_end
        return pages
