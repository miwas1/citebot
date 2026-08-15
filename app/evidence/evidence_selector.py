"""Bounded claim-to-context candidate selection."""

from __future__ import annotations

import re

from app.agents.schemas import Citation, ResearchContext
from app.evidence.schemas import AtomicClaim, EvidenceCandidate


class EvidenceSelector:
    """Select at most a small number of already retrieved contexts per claim."""

    def select(
        self,
        claims: list[AtomicClaim],
        contexts: list[ResearchContext],
        citations: list[Citation],
        max_candidates: int = 8,
    ) -> dict[str, list[EvidenceCandidate]]:
        """Rank candidates by citation match plus bounded lexical overlap."""

        citation_by_id = {citation.citation_id: citation for citation in citations}
        selected: dict[str, list[EvidenceCandidate]] = {}
        for claim in claims:
            candidates: list[EvidenceCandidate] = []
            claim_tokens = set(re.findall(r"[a-z0-9]+", claim.text.lower()))
            for rank, context in enumerate(contexts, start=1):
                overlap = _overlap(claim_tokens, context.text)
                citation_bonus = 0.5 if any(
                    citation_by_id.get(citation_id, None)
                    and citation_by_id[citation_id].chunk_id == context.chunk_id
                    for citation_id in claim.citation_ids
                ) else 0.0
                score = overlap + citation_bonus + max(0.0, context.score) * 0.1
                if score <= 0:
                    continue
                candidates.append(
                    EvidenceCandidate(
                        claim_id=claim.claim_id,
                        chunk_id=context.chunk_id,
                        text=context.text,
                        score=score,
                        source_anchor_ids=list(context.metadata.get("source_anchor_ids") or []),
                        rank=rank,
                    )
                )
            selected[claim.claim_id] = sorted(
                candidates,
                key=lambda candidate: candidate.score,
                reverse=True,
            )[:max_candidates]
        return selected


def _overlap(tokens: set[str], text: str) -> float:
    """Return claim-token coverage in one context."""

    if not tokens:
        return 0.0
    context_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return len(tokens & context_tokens) / len(tokens)
