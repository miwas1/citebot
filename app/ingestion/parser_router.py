"""Deterministic parser quality routing and recorded fallback decisions."""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.parsers.base import DocumentParser
from app.ingestion.schemas import LoadedDocument


@dataclass(frozen=True, slots=True)
class ParserDecision:
    """Recorded parser selection and reason for one candidate document."""

    parser_name: str
    parser_version: str
    reason: str
    quality_signals: dict[str, float | bool]


class ParserRouter:
    """Choose the lightest viable parser using deterministic quality signals."""

    def __init__(self, parsers: list[DocumentParser]) -> None:
        self._parsers = parsers

    def choose(self, document: LoadedDocument) -> tuple[DocumentParser, ParserDecision]:
        """Select the first parser that can handle the media type and record why."""

        media_type = document.structured.media_type if document.structured else None
        for parser in self._parsers:
            if parser.can_parse(media_type):
                coverage = min(1.0, len(document.text.strip()) / 100.0)
                decision = ParserDecision(
                    parser_name=parser.name,
                    parser_version=parser.version,
                    reason=(
                        "structured candidate available"
                        if document.structured
                        else "native fast path"
                    ),
                    quality_signals={
                        "native_character_coverage": coverage,
                        "has_structured_candidate": document.structured is not None,
                    },
                )
                return parser, decision
        raise ValueError(f"No parser can handle media type: {media_type}")
