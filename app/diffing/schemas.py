"""Typed document difference contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ElementSnapshot(BaseModel):
    """Comparable structured element snapshot."""

    element_id: str
    element_type: str
    text: str
    section_path: list[str] = Field(default_factory=list)
    page: int | None = None
    anchor_id: str | None = None


class ElementDiff(BaseModel):
    """One exact element-level change."""

    element_diff_id: str
    old_element_id: str | None = None
    new_element_id: str | None = None
    operation: Literal["added", "removed", "modified", "moved", "unchanged"]
    old_text: str | None = None
    new_text: str | None = None
    opcodes: list[dict[str, object]] = Field(default_factory=list)
    semantic_class: str | None = None
    impact: Literal["critical", "material", "minor", "unknown"] = "unknown"
    old_anchor_id: str | None = None
    new_anchor_id: str | None = None


class DocumentDiff(BaseModel):
    """Complete version comparison with exact changes as the authority."""

    diff_id: str
    old_version_id: str
    new_version_id: str
    elements: list[ElementDiff] = Field(default_factory=list)
    summary: str = ""
    matcher_version: str = "v1"
