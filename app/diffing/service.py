"""Deterministic structured-element diffing."""

from __future__ import annotations

from difflib import SequenceMatcher
from hashlib import sha256
from uuid import uuid4

from app.diffing.schemas import DocumentDiff, ElementDiff, ElementSnapshot


class DocumentDiffService:
    """Compare matched structured elements before asking a model to classify impact."""

    def compare(
        self,
        old_version_id: str,
        new_version_id: str,
        old_elements: list[ElementSnapshot],
        new_elements: list[ElementSnapshot],
    ) -> DocumentDiff:
        """Return exact added, removed, modified, and moved element changes."""

        new_by_hash = self._group_by_hash(new_elements)
        matched_old: set[str] = set()
        matched_new: set[str] = set()
        diffs: list[ElementDiff] = []
        for old in old_elements:
            exact = self._take_match(new_by_hash.get(self._text_hash(old.text), []), matched_new)
            if exact is not None:
                matched_old.add(old.element_id)
                matched_new.add(exact.element_id)
                operation = "unchanged" if old.section_path == exact.section_path else "moved"
                diffs.append(
                    ElementDiff(
                        element_diff_id=uuid4().hex,
                        old_element_id=old.element_id,
                        new_element_id=exact.element_id,
                        operation=operation,
                        old_text=old.text,
                        new_text=exact.text,
                        old_anchor_id=old.anchor_id,
                        new_anchor_id=exact.anchor_id,
                    )
                )
                continue
            candidate = self._best_candidate(old, new_elements, matched_new)
            if candidate is not None and self._similarity(old.text, candidate.text) >= 0.45:
                matched_old.add(old.element_id)
                matched_new.add(candidate.element_id)
                diffs.append(
                    ElementDiff(
                        element_diff_id=uuid4().hex,
                        old_element_id=old.element_id,
                        new_element_id=candidate.element_id,
                        operation="modified",
                        old_text=old.text,
                        new_text=candidate.text,
                        opcodes=self._opcodes(old.text, candidate.text),
                        old_anchor_id=old.anchor_id,
                        new_anchor_id=candidate.anchor_id,
                    )
                )
            else:
                diffs.append(
                    ElementDiff(
                        element_diff_id=uuid4().hex,
                        old_element_id=old.element_id,
                        operation="removed",
                        old_text=old.text,
                        old_anchor_id=old.anchor_id,
                    )
                )
        for element in new_elements:
            if element.element_id not in matched_new:
                diffs.append(
                    ElementDiff(
                        element_diff_id=uuid4().hex,
                        new_element_id=element.element_id,
                        operation="added",
                        new_text=element.text,
                        new_anchor_id=element.anchor_id,
                    )
                )
        modified = sum(item.operation == "modified" for item in diffs)
        added = sum(item.operation == "added" for item in diffs)
        removed = sum(item.operation == "removed" for item in diffs)
        return DocumentDiff(
            diff_id=uuid4().hex,
            old_version_id=old_version_id,
            new_version_id=new_version_id,
            elements=diffs,
            summary=f"{added} added, {removed} removed, {modified} modified",
        )

    def _group_by_hash(self, elements: list[ElementSnapshot]) -> dict[str, list[ElementSnapshot]]:
        grouped: dict[str, list[ElementSnapshot]] = {}
        for element in elements:
            grouped.setdefault(self._text_hash(element.text), []).append(element)
        return grouped

    def _take_match(
        self,
        candidates: list[ElementSnapshot],
        matched: set[str],
    ) -> ElementSnapshot | None:
        return next(
            (candidate for candidate in candidates if candidate.element_id not in matched),
            None,
        )

    def _best_candidate(
        self,
        old: ElementSnapshot,
        new_elements: list[ElementSnapshot],
        matched: set[str],
    ) -> ElementSnapshot | None:
        candidates = [
            element
            for element in new_elements
            if element.element_id not in matched and element.element_type == old.element_type
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda element: self._similarity(old.text, element.text)
            + (0.1 if old.section_path == element.section_path else 0),
        )

    def _similarity(self, left: str, right: str) -> float:
        return SequenceMatcher(None, left.lower(), right.lower()).ratio()

    def _opcodes(self, left: str, right: str) -> list[dict[str, object]]:
        matcher = SequenceMatcher(None, left, right)
        return [
            {"tag": tag, "old_start": i1, "old_end": i2, "new_start": j1, "new_end": j2}
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
            if tag != "equal"
        ]

    def _text_hash(self, text: str) -> str:
        return sha256(" ".join(text.split()).lower().encode("utf-8")).hexdigest()
