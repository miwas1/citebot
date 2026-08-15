"""Citation verification that checks answers against retrieved source context."""

from __future__ import annotations

import re
from typing import Protocol

from app.agents.schemas import (
    Citation,
    CitationVerificationResult,
    ClaimVerification,
    ResearchAnswer,
    ResearchContext,
)


class NliVerifierProtocol(Protocol):
    """Minimal async contract for an optional local NLI verifier."""

    async def verify(self, claim: str, evidence: str) -> dict[str, float | str]:
        """Score one claim/evidence pair."""


class CitationVerifier:
    """Verify that answer citations map back to retrieved or tool-produced context."""

    def __init__(
        self,
        nli_verifier: NliVerifierProtocol | None = None,
        max_nli_pairs: int = 32,
    ) -> None:
        self._nli_verifier = nli_verifier
        self._max_nli_pairs = max_nli_pairs

    async def verify(
        self,
        answer: ResearchAnswer,
        contexts: list[ResearchContext],
    ) -> CitationVerificationResult:
        """Return per-citation verdicts and an overall groundedness result."""

        contexts_by_chunk = {context.chunk_id: context for context in contexts}
        claims: list[ClaimVerification] = []
        unsupported_citation_ids: list[str] = []
        answer_sentences = _split_claims(answer.direct_answer)
        nli_pairs = 0
        for index, citation in enumerate(answer.citations, start=1):
            context = contexts_by_chunk.get(citation.chunk_id)
            claim_text = _best_claim_for_context(answer_sentences, context, citation)
            verdict, confidence, failure_reason, checks = _verify_single_citation(
                claim_text,
                citation.support_span,
                context,
            )
            if (
                self._nli_verifier is not None
                and context is not None
                and verdict in {"unsupported", "partially_supported"}
                and nli_pairs < self._max_nli_pairs
            ):
                nli_pairs += 1
                try:
                    nli_scores = await self._nli_verifier.verify(claim_text, context.text)
                    checks["nli"] = nli_scores
                    entailment = float(nli_scores.get("entailment", 0.0))
                    contradiction = float(nli_scores.get("contradiction", 0.0))
                    if contradiction >= 0.75:
                        verdict = "contradicted"
                        confidence = contradiction
                        failure_reason = "NLI verifier found contradictory evidence."
                    elif entailment >= 0.75:
                        verdict = "supported"
                        confidence = entailment
                        failure_reason = None
                except Exception as error:
                    checks["nli_error"] = str(error)
            if verdict in {"unsupported", "contradicted", "insufficient"}:
                unsupported_citation_ids.append(citation.citation_id)
            claims.append(
                ClaimVerification(
                    citation_id=citation.citation_id,
                    claim_id=f"claim-{index}",
                    claim_text=claim_text,
                    supporting_chunk_ids=[citation.chunk_id] if context else [],
                    verdict=verdict,
                    confidence=confidence,
                    failure_reason=failure_reason,
                    evidence_span=citation.support_span,
                    deterministic_checks=checks,
                )
            )
        if not claims and answer.direct_answer.strip():
            claims.append(
                ClaimVerification(
                    citation_id="unattributed",
                    claim_id="claim-1",
                    claim_text=answer.direct_answer.strip(),
                    verdict="insufficient",
                    confidence=0.0,
                    failure_reason="Answer contains no citations.",
                )
            )
            unsupported_citation_ids.append("unattributed")
        overall_verdict = _overall_verdict(claims)
        supported_count = sum(
            1 for claim in claims if claim.verdict in {"supported", "partially_supported"}
        )
        return CitationVerificationResult(
            overall_verdict=overall_verdict,
            claims=claims,
            unsupported_citation_ids=unsupported_citation_ids,
            verification_version="lexical-v2",
            evidence_coverage=(supported_count / len(claims)) if claims else 0.0,
            contradiction_count=sum(1 for claim in claims if claim.verdict == "contradicted"),
        )


def _verify_single_citation(
    answer_text: str,
    support_span: str,
    context: ResearchContext | None,
) -> tuple[str, float, str | None, dict[str, object]]:
    """Verify one citation against its matched context chunk when available."""

    if context is None:
        return (
            "unsupported",
            0.0,
            "Citation chunk was not part of retrieved context.",
            {"context_present": False},
        )
    normalized_context = context.text.lower()
    normalized_support = support_span.lower()
    claim_numbers = _numeric_tokens(answer_text)
    context_numbers = _numeric_tokens(context.text)
    number_mismatch = bool(claim_numbers - context_numbers)
    checks: dict[str, object] = {
        "context_present": True,
        "exact_evidence_span": bool(
            normalized_support and normalized_support in normalized_context
        ),
        "claim_numbers": sorted(claim_numbers),
        "context_numbers": sorted(context_numbers),
        "numeric_consistent": not number_mismatch,
    }
    if number_mismatch:
        return (
            "contradicted",
            0.92,
            "Claim contains numeric values absent from the cited context.",
            checks,
        )
    if normalized_support and normalized_support in normalized_context:
        return "supported", 0.98, None, checks
    overlap = _token_overlap(answer_text, context.text)
    checks["token_overlap"] = overlap
    if overlap >= 0.4:
        return (
            "partially_supported",
            min(0.85, overlap),
            "Claim only partially overlaps retrieved context.",
            checks,
        )
    if context.source_type == "web" and context.fetched_at is None:
        return "stale", 0.2, "Web citation is missing a retrieval timestamp.", checks
    return "unsupported", overlap, "Claim text is not supported by the cited chunk.", checks


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


def _split_claims(text: str) -> list[str]:
    """Split an answer into bounded sentence-like claim candidates."""

    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]


def _best_claim_for_context(
    sentences: list[str],
    context: ResearchContext | None,
    citation: Citation,
) -> str:
    """Choose the answer sentence most related to a cited context."""

    if context is None or not sentences:
        return citation.support_span
    ranked = sorted(
        sentences,
        key=lambda sentence: _token_overlap(sentence, context.text),
        reverse=True,
    )
    return ranked[0] if ranked else citation.support_span


def _numeric_tokens(text: str) -> set[str]:
    """Return normalized numeric tokens for conservative contradiction checks."""

    return set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text))
