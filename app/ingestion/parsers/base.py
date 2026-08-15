"""Interfaces for optional structured-document parser adapters."""

from __future__ import annotations

from typing import Protocol

from app.ingestion.schemas import LoadedDocument


class DocumentParser(Protocol):
    """Parser adapter contract used by the quality router."""

    name: str
    version: str

    def can_parse(self, media_type: str | None) -> bool:
        """Return whether the adapter supports a document type."""

    def parse(self, document: LoadedDocument) -> LoadedDocument:
        """Return a structured candidate without mutating the input."""
