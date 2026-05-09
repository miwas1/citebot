"""Citation verification that checks answers against retrieved source context."""

from __future__ import annotations

import re

from app.agents.schemas import (
    CitationVerificationResult,
    ClaimVerification,
    ResearchAnswer,
    ResearchContext,
)


class CitationVerifier:
    """Verify that answer citations map back to retrieved or tool-produced context."""

    async def verify(
        self,
        answer: ResearchAnswer,
        contexts: list[ResearchContext],
    ) -> CitationVerificationResult:
        """Return per-citation verdicts and an overall groundedness result."""

        contexts_by_chunk = {context.chunk_id: context for context in contexts}
        claims: list[ClaimVerification] = []
        unsupported_citation_ids: list[str] = []
        for citation in answer.citations:
            context = contexts_by_chunk.get(citation.chunk_id)
            verdict, confidence, failure_reason = _verify_single_citation(
                answer.direct_answer,
                citation.support_span,
                context,
            )
            if verdict == "unsupported":
                unsupported_citation_ids.append(citation.citation_id)
            claims.append(
                ClaimVerification(
                    citation_id=citation.citation_id,
                    claim_text=citation.support_span,
                    supporting_chunk_ids=[citation.chunk_id] if context else [],
                    verdict=verdict,
                    confidence=confidence,
                    failure_reason=failure_reason,
                )
            )
        overall_verdict = _overall_verdict(claims)
        return CitationVerificationResult(
            overall_verdict=overall_verdict,
            claims=claims,
            unsupported_citation_ids=unsupported_citation_ids,
        )


def _verify_single_citation(
    answer_text: str,
    support_span: str,
    context: ResearchContext | None,
) -> tuple[str, float, str | None]:
    """Verify one citation against its matched context chunk when available."""

    if context is None:
        return "unsupported", 0.0, "Citation chunk was not part of retrieved context."
    normalized_context = context.text.lower()
    normalized_support = support_span.lower()
    if normalized_support and normalized_support in normalized_context:
        return "supported", 0.98, None
    overlap = _token_overlap(answer_text, context.text)
    if overlap >= 0.4:
        return (
            "partially_supported",
            min(0.85, overlap),
            "Claim only partially overlaps retrieved context.",
        )
    if context.source_type == "web" and context.fetched_at is None:
        return "stale", 0.2, "Web citation is missing a retrieval timestamp."
    return "unsupported", overlap, "Claim text is not supported by the cited chunk."


def _token_overlap(left: str, right: str) -> float:
    """Compute a simple set-overlap score for claim support heuristics."""

    left_tokens = set(re.findall(r"[a-z0-9]+", left.lower()))
    right_tokens = set(re.findall(r"[a-z0-9]+", right.lower()))
    if not left_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens)


def _overall_verdict(claims: list[ClaimVerification]) -> str:
    """Collapse per-claim verdicts into a response-level outcome."""

    if not claims:
        return "unsupported"
    verdicts = {claim.verdict for claim in claims}
    if verdicts == {"supported"}:
        return "supported"
    if "supported" in verdicts or "partially_supported" in verdicts:
        return "partially_supported"
    return "unsupported"
