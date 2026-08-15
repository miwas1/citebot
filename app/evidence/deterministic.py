"""Deterministic evidence checks used before optional NLI."""

from __future__ import annotations

import re

from app.evidence.schemas import AtomicClaim, DeterministicCheck, EvidenceCandidate


class DeterministicVerifier:
    """Check exact spans, numeric consistency, negation, and provenance presence."""

    def check(
        self,
        claim: AtomicClaim,
        candidate: EvidenceCandidate | None,
    ) -> list[DeterministicCheck]:
        """Return auditable checks without making a semantic model judgment."""

        if candidate is None:
            return [
                DeterministicCheck(
                    name="evidence_present",
                    passed=False,
                    severity="critical",
                    details={"reason": "no selected evidence candidate"},
                )
            ]
        claim_numbers = _numbers(claim.text)
        context_numbers = _numbers(candidate.text)
        negation_claim = _negated(claim.text)
        negation_context = _negated(candidate.text)
        return [
            DeterministicCheck(
                name="evidence_present",
                passed=True,
                severity="info",
                details={"chunk_id": candidate.chunk_id},
            ),
            DeterministicCheck(
                name="numeric_consistency",
                passed=claim_numbers <= context_numbers,
                severity="critical" if claim_numbers - context_numbers else "info",
                details={
                    "claim_numbers": sorted(claim_numbers),
                    "context_numbers": sorted(context_numbers),
                },
            ),
            DeterministicCheck(
                name="negation_consistency",
                passed=negation_claim == negation_context,
                severity="critical" if negation_claim != negation_context else "info",
                details={"claim_negated": negation_claim, "context_negated": negation_context},
            ),
            DeterministicCheck(
                name="source_anchor_present",
                passed=bool(candidate.source_anchor_ids),
                severity="warning",
                details={"anchor_count": len(candidate.source_anchor_ids)},
            ),
        ]


def _numbers(text: str) -> set[str]:
    """Extract normalized numeric tokens."""

    return set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text))


def _negated(text: str) -> bool:
    """Detect a conservative set of explicit negation markers."""

    return bool(re.search(r"\b(?:not|no|never|without|cannot|can't)\b", text.lower()))
