"""Stable source-anchor construction shared by chunking and persistence."""

from __future__ import annotations

from hashlib import sha256

from app.ingestion.schemas import CanonicalDocument, DocumentElement


def anchor_id(version_id: str, element_id: str) -> str:
    """Build a stable anchor identity scoped to one immutable document version."""

    return f"{version_id}:{element_id}"


def text_hash(text: str) -> str:
    """Normalize and hash observed source text."""

    return sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def element_anchors(
    document: CanonicalDocument,
) -> list[tuple[str, DocumentElement, int | None]]:
    """Yield version-scoped element anchors with page provenance."""

    if document.structured is None:
        return []
    anchors: list[tuple[str, DocumentElement, int | None]] = []
    for page in document.structured.pages:
        for element in page.elements:
            anchors.append(
                (anchor_id(document.content_hash, element.element_id), element, page.page_number)
            )
    return anchors
