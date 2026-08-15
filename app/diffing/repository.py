"""Persistence for exact document diffs."""

from __future__ import annotations

from app.db.models import DocumentDiffRecord, ElementDiffRecord
from app.db.session import DatabaseSessionManager
from app.diffing.schemas import DocumentDiff


class DiffRepository:
    """Store version pair metadata and every source-linked element operation."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        self._session_manager = session_manager

    async def save(self, diff: DocumentDiff) -> str:
        """Persist a completed diff as an immutable comparison artifact."""

        async with self._session_manager.session() as session:
            session.add(
                DocumentDiffRecord(
                    diff_id=diff.diff_id,
                    old_version_id=diff.old_version_id,
                    new_version_id=diff.new_version_id,
                    matcher_version=diff.matcher_version,
                    summary_json={"summary": diff.summary, "element_count": len(diff.elements)},
                )
            )
            for item in diff.elements:
                session.add(
                    ElementDiffRecord(
                        element_diff_id=item.element_diff_id,
                        diff_id=diff.diff_id,
                        old_element_id=item.old_element_id,
                        new_element_id=item.new_element_id,
                        operation=item.operation,
                        exact_diff_json=item.model_dump(mode="json"),
                        semantic_class=item.semantic_class,
                        impact=item.impact,
                        old_anchor_id=item.old_anchor_id,
                        new_anchor_id=item.new_anchor_id,
                    )
                )
        return diff.diff_id
