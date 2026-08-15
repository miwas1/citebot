"""Conservative policy for converting checks and candidates into verdicts."""

from __future__ import annotations

from app.evidence.schemas import (
    AtomicClaim,
    DeterministicCheck,
    EvidenceCandidate,
    VerificationDecision,
)


class VerificationAggregator:
    """Never upgrade a failed critical deterministic check to supported."""

    def decide(
        self,
        claim: AtomicClaim,
        candidates: list[EvidenceCandidate],
        checks: list[DeterministicCheck],
    ) -> VerificationDecision:
        """Return supported, contradicted, or insufficient using explicit policy."""

        critical_failures = [
            check
            for check in checks
            if not check.passed and check.severity == "critical"
        ]
        if critical_failures:
            verdict = "contradicted" if any(
                check.name in {"numeric_consistency", "negation_consistency"}
                for check in critical_failures
            ) else "insufficient"
            return VerificationDecision(
                claim_id=claim.claim_id,
                verdict=verdict,
                confidence=0.92 if verdict == "contradicted" else 0.1,
                evidence=candidates[:1],
                checks=checks,
                reason="; ".join(check.name for check in critical_failures),
            )
        if not candidates:
            return VerificationDecision(
                claim_id=claim.claim_id,
                verdict="insufficient",
                confidence=0.0,
                checks=checks,
                reason="No evidence candidate was selected.",
            )
        overlap = candidates[0].score
        if overlap >= 0.65:
            return VerificationDecision(
                claim_id=claim.claim_id,
                verdict="supported",
                confidence=min(0.98, overlap),
                evidence=candidates[:1],
                checks=checks,
            )
        return VerificationDecision(
            claim_id=claim.claim_id,
            verdict="partially_supported",
            confidence=min(0.75, overlap),
            evidence=candidates[:1],
            checks=checks,
            reason="Evidence overlaps the claim but does not fully cover it.",
        )
