"""Local durable storage for raw text and structured document artifacts."""

import json
from pathlib import Path
from typing import Any


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

    async def store_structured(
        self,
        document_id: str,
        payload: dict[str, Any],
        base_path: Path,
    ) -> str:
        """Write a versioned structured-document JSON artifact atomically."""

        base_path.mkdir(parents=True, exist_ok=True)
        output_path = base_path / f"{document_id}.structured.json"
        temporary_path = output_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
        return str(output_path)
