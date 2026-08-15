"""Orchestrator for the explicit claim/evidence pipeline stages."""

from __future__ import annotations

from app.agents.schemas import (
    CitationVerificationResult,
    ResearchAnswer,
    ResearchContext,
)
from app.evidence.aggregator import VerificationAggregator
from app.evidence.claim_extractor import ClaimExtractor
from app.evidence.deterministic import DeterministicVerifier
from app.evidence.evidence_selector import EvidenceSelector
from app.evidence.refiner import AnswerRefiner
from app.evidence.schemas import AtomicClaim, EvidenceCandidate, VerificationDecision


class EvidenceService:
    """Run extraction, selection, deterministic checks, and policy aggregation."""

    def __init__(self) -> None:
        self.extractor = ClaimExtractor()
        self.selector = EvidenceSelector()
        self.deterministic = DeterministicVerifier()
        self.aggregator = VerificationAggregator()
        self.refiner = AnswerRefiner()

    def extract(self, answer: ResearchAnswer) -> list[AtomicClaim]:
        """Extract atomic claims from an answer."""

        return self.extractor.extract(answer)

    def select(
        self,
        claims: list[AtomicClaim],
        answer: ResearchAnswer,
        contexts: list[ResearchContext],
    ) -> dict[str, list[EvidenceCandidate]]:
        """Select bounded evidence candidates from retrieved contexts."""

        return self.selector.select(claims, contexts, answer.citations)

    def verify(
        self,
        claims: list[AtomicClaim],
        candidates: dict[str, list[EvidenceCandidate]],
    ) -> list[VerificationDecision]:
        """Apply deterministic checks and conservative aggregation per claim."""

        decisions: list[VerificationDecision] = []
        for claim in claims:
            selected = candidates.get(claim.claim_id, [])
            checks = self.deterministic.check(claim, selected[0] if selected else None)
            decisions.append(self.aggregator.decide(claim, selected, checks))
        return decisions

    def summary(self, decisions: list[VerificationDecision]) -> CitationVerificationResult:
        """Build a response-level summary for callers that do not need citations."""

        supported = sum(decision.verdict == "supported" for decision in decisions)
        contradictions = sum(decision.verdict == "contradicted" for decision in decisions)
        verdict = (
            "supported"
            if decisions and supported == len(decisions)
            else "partially_supported"
        )
        if not supported and decisions:
            verdict = "unsupported"
        return CitationVerificationResult(
            overall_verdict=verdict,
            verification_version="evidence-pipeline-v1",
            evidence_coverage=supported / len(decisions) if decisions else 0.0,
            contradiction_count=contradictions,
        )

    def apply_decisions(
        self,
        verification: CitationVerificationResult,
        decisions: list[VerificationDecision],
    ) -> CitationVerificationResult:
        """Apply deterministic failures without allowing weaker checks to upgrade claims."""

        decisions_by_claim = {decision.claim_id: decision for decision in decisions}
        claims = []
        for claim in verification.claims:
            decision = decisions_by_claim.get(claim.claim_id or "")
            if decision is None or decision.verdict == "supported":
                claims.append(claim)
                continue
            failed_checks = {
                check.name: check.passed for check in decision.checks
            }
            claims.append(
                claim.model_copy(
                    update={
                        "verdict": decision.verdict,
                        "confidence": min(claim.confidence, decision.confidence),
                        "failure_reason": decision.reason or claim.failure_reason,
                        "deterministic_checks": {
                            **claim.deterministic_checks,
                            **failed_checks,
                        },
                    }
                )
            )
        unsupported_ids = list(verification.unsupported_citation_ids)
        unsupported_ids.extend(
            claim.citation_id
            for claim in claims
            if claim.verdict in {"unsupported", "contradicted", "insufficient"}
            and claim.citation_id not in unsupported_ids
        )
        supported_count = sum(
            claim.verdict in {"supported", "partially_supported"}
            for claim in claims
        )
        overall = (
            "supported"
            if claims and all(claim.verdict == "supported" for claim in claims)
            else "partially_supported"
            if supported_count
            else "unsupported"
        )
        return verification.model_copy(
            update={
                "overall_verdict": overall,
                "claims": claims,
                "unsupported_citation_ids": unsupported_ids,
                "verification_version": "evidence-pipeline-v1+lexical-v2",
                "evidence_coverage": supported_count / len(claims) if claims else 0.0,
                "contradiction_count": sum(
                    claim.verdict == "contradicted" for claim in claims
                ),
            }
        )
