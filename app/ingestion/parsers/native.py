"""Fast-path parser adapter for the built-in native loader."""

from __future__ import annotations

from app.ingestion.schemas import LoadedDocument


class NativeParser:
    """Expose existing loader output through the common adapter contract."""

    name = "native"
    version = "structured-v2"

    def can_parse(self, media_type: str | None) -> bool:
        """Accept local documents already carrying structured output."""

        return media_type in {None, "application/pdf", "text/plain", "text/markdown"}

    def parse(self, document: LoadedDocument) -> LoadedDocument:
        """Return a defensive copy of the native candidate."""

        return document.model_copy(deep=True)
