"""Deterministic page-quality signals for low-resource parser routing."""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.schemas import DocumentElement


@dataclass(frozen=True, slots=True)
class PageQualitySignals:
    """Measured page signals retained with the parser decision."""

    native_character_coverage: float
    replacement_character_rate: float
    image_only: bool
    table_likelihood: float
    column_likelihood: float
    reading_order_discontinuity: float

    def as_dict(self) -> dict[str, float | bool]:
        """Return a JSON-compatible signal mapping."""

        return {
            "native_character_coverage": self.native_character_coverage,
            "replacement_character_rate": self.replacement_character_rate,
            "image_only": self.image_only,
            "table_likelihood": self.table_likelihood,
            "column_likelihood": self.column_likelihood,
            "reading_order_discontinuity": self.reading_order_discontinuity,
        }


def assess_page(text: str, elements: list[DocumentElement]) -> PageQualitySignals:
    """Measure cheap quality signals before enabling OCR or layout parsing."""

    compact = text.strip()
    coverage = min(1.0, len(compact) / 100.0)
    replacement_rate = compact.count("�") / max(1, len(compact))
    table_markers = sum(
        1
        for element in elements
        if element.element_type in {"table", "table_row", "table_cell"}
    )
    x_positions = [element.bbox[0] for element in elements if element.bbox is not None]
    unique_columns = len({round(position / 20) for position in x_positions})
    column_likelihood = min(1.0, max(0, unique_columns - 1) / 3)
    reading_jumps = sum(
        current.reading_order > previous.reading_order + 1
        for previous, current in zip(elements, elements[1:], strict=False)
    )
    discontinuity = reading_jumps / max(1, len(elements) - 1)
    return PageQualitySignals(
        native_character_coverage=coverage,
        replacement_character_rate=replacement_rate,
        image_only=not bool(compact),
        table_likelihood=min(1.0, table_markers / 8),
        column_likelihood=column_likelihood,
        reading_order_discontinuity=discontinuity,
    )


def route_page(signals: PageQualitySignals, ocr_threshold: float) -> str:
    """Choose the lightest route and make fallback reasons explicit."""

    if signals.image_only or signals.native_character_coverage < ocr_threshold:
        return "ocr"
    if signals.replacement_character_rate > 0.01:
        return "layout"
    if signals.table_likelihood >= 0.5 or signals.column_likelihood >= 0.67:
        return "layout"
    return "native"
