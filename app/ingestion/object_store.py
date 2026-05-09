"""Local durable storage for raw or normalized document text."""

from pathlib import Path


class LocalObjectStore:
    """Persist extracted document text to the local filesystem."""

    def __init__(self, base_path: Path) -> None:
        """Store the filesystem base path for raw document payloads."""

        self._base_path = base_path

    async def initialize(self) -> None:
        """Ensure the underlying storage directory exists."""

        self._base_path.mkdir(parents=True, exist_ok=True)

    async def store_document(self, document_id: str, text: str) -> str:
        """Write the canonical document text to durable local storage."""

        output_path = self._base_path / f"{document_id}.txt"
        output_path.write_text(text, encoding="utf-8")
        return str(output_path)
