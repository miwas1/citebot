"""Low-resource atomic claim extraction for generated answers."""

from __future__ import annotations

import re

from app.agents.schemas import Citation, ResearchAnswer
from app.evidence.schemas import AtomicClaim


class ClaimExtractor:
    """Split prose into bounded claims and classify high-risk claim forms."""

    def extract(self, answer: ResearchAnswer) -> list[AtomicClaim]:
        """Return one claim per sentence, preserving citation provenance when possible."""

        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer.direct_answer)
            if sentence.strip()
        ]
        claims: list[AtomicClaim] = []
        for index, text in enumerate(sentences, start=1):
            claims.append(
                AtomicClaim(
                    claim_id=f"claim-{index}",
                    text=text,
                    claim_type=_classify_claim(text),
                    importance="material" if _is_material(text) else "supporting",
                    citation_ids=_nearest_citation_ids(text, answer.citations),
                )
            )
        return claims


def _classify_claim(text: str) -> str:
    """Classify common high-risk claim signals without another model call."""

    lowered = text.lower()
    if re.search(r"\b\d+(?:[.,]\d+)?%?\b", text) or any(
        token in lowered for token in ("amount", "price", "cost", "months", "years")
    ):
        return "numeric"
    if any(token in lowered for token in ("before", "after", "during", "effective", "date")):
        return "temporal"
    if any(token in lowered for token in ("more than", "less than", "compared", "versus")):
        return "comparative"
    if any(token in lowered for token in ("should", "recommend", "must consider")):
        return "recommendation"
    if any(
        token in lowered
        for token in ("uncertain", "not enough", "limitation", "cannot verify")
    ):
        return "limitation"
    return "factual"


def _is_material(text: str) -> bool:
    """Treat numeric, temporal, legal, and negated statements as material."""

    lowered = text.lower()
    return bool(
        re.search(r"\b\d+(?:[.,]\d+)?%?\b", text)
        or any(token in lowered for token in ("not", "no ", "shall", "liable", "term", "date"))
    )


def _nearest_citation_ids(text: str, citations: list[Citation]) -> list[str]:
    """Select citations with the greatest lexical overlap for this sentence."""

    if not citations:
        return []
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    scored = sorted(
        citations,
        key=lambda citation: len(
            tokens & set(re.findall(r"[a-z0-9]+", citation.support_span.lower()))
        ),
        reverse=True,
    )
    return [citation.citation_id for citation in scored[:1]]
