"""Deterministic representations for structured tables."""

from __future__ import annotations

from app.ingestion.schemas import DocumentTable


def table_to_markdown(table: DocumentTable) -> str:
    """Serialize a table with repeated headers for language-model context."""

    columns = max(
        len(table.headers),
        *(len(row) for row in table.rows),
        1,
    )
    headers = _pad(table.headers, columns)
    lines = [
        f"Table {table.table_id}",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_pad(row, columns)) + " |" for row in table.rows)
    return "\n".join(lines)


def table_row_chunks(table: DocumentTable) -> list[tuple[int, str]]:
    """Return row-level search units retaining the table header."""

    header = " | ".join(table.headers)
    return [
        (index, f"Table {table.table_id}; columns: {header}; row: {' | '.join(row)}")
        for index, row in enumerate(table.rows, start=1)
    ]


def _pad(values: list[str], size: int) -> list[str]:
    """Pad cells so malformed rows remain rectangular and deterministic."""

    return [*(values[:size]), *("" for _ in range(max(0, size - len(values))))]
