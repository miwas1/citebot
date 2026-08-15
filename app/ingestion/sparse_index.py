"""SQLite FTS5 sparse retrieval for the local single-host runtime."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.ingestion.schemas import (
    DEFAULT_PROJECT_ID,
    CanonicalDocument,
    ChunkPayload,
    RetrievalFilters,
    SearchResult,
)


class SparseIndex:
    """Persist chunk metadata in SQLite and search it through FTS5.

    ``index_path`` remains the public configuration knob for compatibility. A
    sibling ``.sqlite3`` file is used so an existing JSON index is never
    overwritten; it is migrated once on first initialization.
    """

    def __init__(self, index_path: Path) -> None:
        self._legacy_path = index_path
        self._db_path = index_path.with_suffix(".sqlite3")

    async def initialize(self) -> None:
        """Create the FTS schema and migrate a legacy JSON index if present."""

        self._initialize_sync()

    async def replace_document_chunks(
        self,
        document: CanonicalDocument,
        chunks: list[ChunkPayload],
    ) -> None:
        """Replace one document atomically in the sparse index."""

        self._replace_document_chunks_sync(document, chunks)

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchResult]:
        """Search indexed chunks with SQLite FTS5 and bounded result materialization."""

        return self._search_sync(query, top_k, filters)

    def _initialize_sync(self) -> None:
        """Create schema and migrate legacy content in one short transaction."""

        legacy_chunks: list[dict[str, Any]] = []
        if self._legacy_path.exists() and not self._db_path.exists():
            try:
                payload = json.loads(self._legacy_path.read_text(encoding="utf-8"))
                chunks = payload.get("chunks", {})
                if isinstance(chunks, dict):
                    legacy_chunks = [
                        value for value in chunks.values() if isinstance(value, dict)
                    ]
            except (OSError, json.JSONDecodeError):
                # A malformed legacy file must not prevent a clean index from
                # being created; the original file remains available for repair.
                legacy_chunks = []

        with self._connect() as connection:
            self._create_schema(connection)
            if legacy_chunks:
                for value in legacy_chunks:
                    self._insert_chunk(connection, value)

    def _replace_document_chunks_sync(
        self,
        document: CanonicalDocument,
        chunks: list[ChunkPayload],
    ) -> None:
        """Mark prior versions stale and insert the new searchable version."""

        with self._connect() as connection:
            self._create_schema(connection)
            connection.execute(
                "UPDATE sparse_chunks SET is_current = 0 WHERE project_id = ? AND document_id = ?",
                (document.project_id, document.document_id),
            )
            for chunk in chunks:
                value = {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "project_id": document.project_id,
                    "title": chunk.title,
                    "source_uri": chunk.source_uri,
                    "location_marker": chunk.location_marker,
                    "access_policy": document.access_policy,
                    "embedding_version": chunk.embedding_version,
                    "index_version": chunk.index_version,
                    "section": chunk.section,
                    "page": chunk.page,
                    "metadata": document.metadata,
                    "text": chunk.text,
                    "element_ids": chunk.element_ids,
                    "bbox_refs": chunk.bbox_refs,
                    "extraction_method": chunk.extraction_method,
                    "min_confidence": chunk.min_confidence,
                    "parent_chunk_id": chunk.parent_chunk_id,
                    "chunk_level": chunk.chunk_level,
                    "heading_path": chunk.heading_path,
                    "content_hash": chunk.content_hash,
                    "version_id": chunk.version_id,
                    "is_current": chunk.is_current,
                    "ordinal": chunk.ordinal,
                    "source_anchor_ids": chunk.source_anchor_ids,
                }
                self._insert_chunk(connection, value)

    def _search_sync(
        self,
        query: str,
        top_k: int,
        filters: RetrievalFilters | None,
    ) -> list[SearchResult]:
        """Run one bounded FTS query and convert rows into API results."""

        filters = filters or RetrievalFilters()
        terms = self._tokenize(query)
        if not terms:
            return []
        match_query = " OR ".join(f'"{term}"' for term in terms)
        conditions = ["sparse_chunks_fts MATCH ?", "c.project_id = ?"]
        parameters: list[Any] = [match_query]
        parameters.append(filters.project_id if filters else DEFAULT_PROJECT_ID)
        if filters is not None:
            self._add_filter(
                conditions,
                parameters,
                "c.document_id",
                filters.document_ids,
            )
            self._add_filter(
                conditions,
                parameters,
                "c.source_uri",
                filters.source_uris,
            )
            self._add_filter(
                conditions,
                parameters,
                "c.access_policy",
                filters.access_policies,
            )
            if filters.embedding_version is not None:
                conditions.append("c.embedding_version = ?")
                parameters.append(filters.embedding_version)
            if filters.index_version is not None:
                conditions.append("c.index_version = ?")
                parameters.append(filters.index_version)
            self._add_filter(conditions, parameters, "c.version_id", filters.version_ids)
            if filters.current_only:
                conditions.append("c.is_current = 1")
            self._add_filter(conditions, parameters, "c.chunk_level", filters.chunk_levels)
        parameters.append(max(1, min(top_k, 50)))
        statement = f"""
            SELECT
                c.chunk_id, c.document_id, c.project_id, c.title, c.source_uri,
                c.location_marker, c.access_policy, c.embedding_version,
                c.index_version, c.section, c.page, c.metadata_json,
                c.text, c.element_ids_json, c.bbox_refs_json,
                c.extraction_method, c.min_confidence, c.parent_chunk_id,
                c.chunk_level, c.heading_path_json, c.content_hash, c.version_id,
                c.is_current, c.ordinal, c.source_anchor_ids_json,
                bm25(sparse_chunks_fts) AS rank_score
            FROM sparse_chunks_fts
            JOIN sparse_chunks AS c ON c.chunk_id = sparse_chunks_fts.chunk_id
            WHERE {' AND '.join(conditions)}
            ORDER BY rank_score ASC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        return [self._row_to_result(row) for row in rows]

    def _insert_chunk(self, connection: sqlite3.Connection, value: dict[str, Any]) -> None:
        """Insert one metadata row and its corresponding FTS row."""

        chunk_id = str(value.get("chunk_id", ""))
        connection.execute(
            """
            INSERT OR REPLACE INTO sparse_chunks (
                chunk_id, document_id, project_id, title, source_uri, location_marker,
                access_policy, embedding_version, index_version, section, page,
                metadata_json, text, element_ids_json, bbox_refs_json,
                extraction_method, min_confidence, parent_chunk_id, chunk_level,
                heading_path_json, content_hash, version_id, is_current, ordinal,
                source_anchor_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                str(value.get("document_id", "")),
                str(value.get("project_id", DEFAULT_PROJECT_ID)),
                str(value.get("title", "")),
                str(value.get("source_uri", "")),
                value.get("location_marker"),
                str(value.get("access_policy", "internal")),
                str(value.get("embedding_version", "")),
                str(value.get("index_version", "")),
                value.get("section"),
                value.get("page"),
                json.dumps(value.get("metadata", {}), ensure_ascii=False),
                str(value.get("text", "")),
                json.dumps(value.get("element_ids", [])),
                json.dumps(value.get("bbox_refs", [])),
                value.get("extraction_method"),
                value.get("min_confidence"),
                value.get("parent_chunk_id"),
                value.get("chunk_level", "window"),
                json.dumps(value.get("heading_path", [])),
                value.get("content_hash"),
                value.get("version_id"),
                1 if value.get("is_current", True) else 0,
                int(value.get("ordinal", 0)),
                json.dumps(value.get("source_anchor_ids", [])),
            ),
        )
        connection.execute(
            "DELETE FROM sparse_chunks_fts WHERE chunk_id = ?",
            (chunk_id,),
        )
        connection.execute(
            "INSERT INTO sparse_chunks_fts (chunk_id, title, text) VALUES (?, ?, ?)",
            (chunk_id, str(value.get("title", "")), str(value.get("text", ""))),
        )

    def _row_to_result(self, row: sqlite3.Row) -> SearchResult:
        """Convert one SQLite row into the shared retrieval result contract."""

        metadata = self._json_object(row["metadata_json"])
        score = max(0.0, -float(row["rank_score"]))
        metadata.update(
            {
                "access_policy": row["access_policy"],
                "embedding_version": row["embedding_version"],
                "index_version": row["index_version"],
                "section": row["section"],
                "page": row["page"],
                "document_metadata": self._json_object(row["metadata_json"]),
                "parent_chunk_id": row["parent_chunk_id"],
                "chunk_level": row["chunk_level"],
                "heading_path": self._json_list(row["heading_path_json"]),
                "version_id": row["version_id"],
                "is_current": bool(row["is_current"]),
                "source_anchor_ids": self._json_list(row["source_anchor_ids_json"]),
            }
        )
        return SearchResult(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            project_id=(row["project_id"] if "project_id" in row.keys() else DEFAULT_PROJECT_ID),
            title=row["title"],
            source_uri=row["source_uri"],
            location_marker=row["location_marker"],
            page=row["page"],
            element_ids=self._json_list(row["element_ids_json"]),
            bbox_refs=self._json_boxes(row["bbox_refs_json"]),
            extraction_method=row["extraction_method"],
            min_confidence=row["min_confidence"],
            parent_chunk_id=row["parent_chunk_id"],
            chunk_level=row["chunk_level"] or "window",
            heading_path=self._json_list(row["heading_path_json"]),
            version_id=row["version_id"],
            is_current=bool(row["is_current"]),
            source_anchor_ids=self._json_list(row["source_anchor_ids_json"]),
            score=score,
            sparse_score=score,
            text=row["text"],
            source_backend="sparse",
            metadata=metadata,
        )

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived WAL connection with a bounded lock wait."""

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        """Create ordinary metadata and FTS5 tables if they are absent."""

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sparse_chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT 'sample-project',
                title TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                location_marker TEXT,
                access_policy TEXT NOT NULL,
                embedding_version TEXT NOT NULL,
                index_version TEXT NOT NULL,
                section TEXT,
                page INTEGER,
                metadata_json TEXT NOT NULL,
                text TEXT NOT NULL,
                element_ids_json TEXT NOT NULL,
                bbox_refs_json TEXT NOT NULL,
                extraction_method TEXT,
                min_confidence REAL,
                parent_chunk_id TEXT,
                chunk_level TEXT NOT NULL DEFAULT 'window',
                heading_path_json TEXT NOT NULL DEFAULT '[]',
                content_hash TEXT,
                version_id TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                ordinal INTEGER NOT NULL DEFAULT 0,
                source_anchor_ids_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS sparse_chunks_document_idx
                ON sparse_chunks(document_id);
            CREATE INDEX IF NOT EXISTS sparse_chunks_project_idx
                ON sparse_chunks(project_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS sparse_chunks_fts
                USING fts5(chunk_id UNINDEXED, title, text);
            """
        )
        existing = {
            row[1] for row in connection.execute("PRAGMA table_info(sparse_chunks)")
        }
        additions = {
            "parent_chunk_id": "TEXT",
            "chunk_level": "TEXT NOT NULL DEFAULT 'window'",
            "heading_path_json": "TEXT NOT NULL DEFAULT '[]'",
            "content_hash": "TEXT",
            "version_id": "TEXT",
            "is_current": "INTEGER NOT NULL DEFAULT 1",
            "ordinal": "INTEGER NOT NULL DEFAULT 0",
            "source_anchor_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "project_id": "TEXT NOT NULL DEFAULT 'sample-project'",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE sparse_chunks ADD COLUMN {name} {definition}")
        connection.execute(
            "UPDATE sparse_chunks SET project_id = ? WHERE project_id = ?",
            ("imported-documents", "legacy-project"),
        )

    @staticmethod
    def _add_filter(
        conditions: list[str],
        parameters: list[Any],
        column: str,
        values: list[str],
    ) -> None:
        """Append a parameterized IN predicate when a filter is present."""

        if values:
            placeholders = ", ".join("?" for _ in values)
            conditions.append(f"{column} IN ({placeholders})")
            parameters.extend(values)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize text into bounded FTS-safe alphanumeric terms."""

        return re.findall(r"[a-z0-9]+", text.lower())[:64]

    @staticmethod
    def _json_object(value: str | None) -> dict[str, Any]:
        """Decode a JSON object with a safe empty fallback."""

        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_list(value: str | None) -> list[str]:
        """Decode a JSON string list with a safe empty fallback."""

        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _json_boxes(value: str | None) -> list[list[float]]:
        """Decode bounding-box arrays for the retrieval response contract."""

        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [list(map(float, box)) for box in parsed if isinstance(box, (list, tuple))]
