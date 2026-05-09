"""Corpus loaders for local file-based sample datasets."""

from __future__ import annotations

import json
from pathlib import Path

from app.ingestion.schemas import LoadedDocument


class LocalCorpusLoader:
    """Load `.txt`, `.md`, and `.json` corpus files from disk."""

    def load(self, source_path: Path) -> list[LoadedDocument]:
        """Load all supported documents from the given file or directory path."""

        if not source_path.exists():
            msg = f"Source path does not exist: {source_path}"
            raise FileNotFoundError(msg)

        files = (
            [source_path]
            if source_path.is_file()
            else sorted(path for path in source_path.rglob("*") if path.is_file())
        )
        documents: list[LoadedDocument] = []
        for file_path in files:
            if file_path.suffix.lower() in {".txt", ".md"}:
                documents.append(self._load_text_document(file_path))
            elif file_path.suffix.lower() == ".json":
                documents.extend(self._load_json_documents(file_path))
        return documents

    def _load_text_document(self, file_path: Path) -> LoadedDocument:
        """Read a plain-text or Markdown document from disk."""

        return LoadedDocument(
            source_uri=str(file_path.resolve()),
            title=file_path.stem.replace("_", " ").strip() or file_path.name,
            text=file_path.read_text(encoding="utf-8"),
            metadata={
                "file_name": file_path.name,
                "file_type": file_path.suffix.lower().lstrip("."),
            },
        )

    def _load_json_documents(self, file_path: Path) -> list[LoadedDocument]:
        """Read one or more documents from a JSON file."""

        payload = json.loads(file_path.read_text(encoding="utf-8"))
        raw_documents = payload if isinstance(payload, list) else [payload]
        documents: list[LoadedDocument] = []
        for index, raw_document in enumerate(raw_documents):
            documents.append(
                LoadedDocument(
                    source_uri=raw_document.get("source_uri")
                    or f"{file_path.resolve()}#{index}",
                    title=raw_document.get("title") or f"{file_path.stem}-{index}",
                    text=raw_document.get("text") or raw_document.get("content") or "",
                    publisher=raw_document.get("publisher"),
                    published_at=raw_document.get("published_at"),
                    access_policy=raw_document.get("access_policy", "internal"),
                    metadata=raw_document.get("metadata", {}),
                )
            )
        return documents
