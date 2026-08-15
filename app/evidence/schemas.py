"""Typed contracts for the claim-to-evidence verification pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AtomicClaim(BaseModel):
    """One independently verifiable statement extracted from an answer."""

    claim_id: str
    text: str
    claim_type: Literal[
        "factual",
        "numeric",
        "temporal",
        "comparative",
        "recommendation",
        "limitation",
    ] = "factual"
    importance: Literal["critical", "material", "supporting", "incidental"] = "material"
    citation_ids: list[str] = Field(default_factory=list)


class EvidenceCandidate(BaseModel):
    """Bounded candidate evidence selected from already retrieved contexts."""

    claim_id: str
    chunk_id: str
    text: str
    score: float
    source_anchor_ids: list[str] = Field(default_factory=list)
    rank: int = 0


class DeterministicCheck(BaseModel):
    """One auditable non-model verification check."""

    name: str
    passed: bool
    severity: Literal["info", "warning", "critical"] = "warning"
    details: dict[str, Any] = Field(default_factory=dict)


class VerificationDecision(BaseModel):
    """Conservative claim-level decision before response formatting."""

    claim_id: str
    verdict: Literal[
        "supported",
        "partially_supported",
        "unsupported",
        "contradicted",
        "insufficient",
        "uncertain",
        "stale",
    ]
    confidence: float = 0.0
    evidence: list[EvidenceCandidate] = Field(default_factory=list)
    checks: list[DeterministicCheck] = Field(default_factory=list)
    reason: str | None = None
