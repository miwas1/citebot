"""Persistence for claim-level evidence ledgers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.agents.schemas import ResearchResponse
from app.db.models import (
    AnalysisRunRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    SourceAnchorRecord,
)
from app.db.session import DatabaseSessionManager


class EvidenceLedgerRepository:
    """Persist an immutable analysis snapshot and its claim-to-anchor links."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        self._session_manager = session_manager

    async def save_response(
        self,
        response: ResearchResponse,
        query: str,
        workflow_id: str = "research",
        embedding_version: str | None = None,
        index_version: str | None = None,
    ) -> str:
        """Write one idempotent response ledger keyed by its trace ID."""

        analysis_run_id = response.trace_id
        async with self._session_manager.session() as session:
            existing = await session.get(AnalysisRunRecord, analysis_run_id)
            if existing is not None:
                return analysis_run_id
            session.add(
                AnalysisRunRecord(
                    analysis_run_id=analysis_run_id,
                    session_id=response.session_id,
                    trace_id=response.trace_id,
                    workflow_id=workflow_id,
                    query=query,
                    status="failed" if response.error else "completed",
                    embedding_version=embedding_version,
                    index_version=index_version,
                    quality_summary_json={
                        "evidence_coverage": response.evidence_coverage,
                        "contradiction_count": response.contradiction_count,
                        "verification_version": response.verification_version,
                    },
                    finished_at=datetime.now(tz=UTC),
                )
            )
            anchor_ids = {
                anchor_id
                for citation in response.answer.citations
                for anchor_id in citation.source_anchor_ids
            }
            if anchor_ids:
                known_anchor_ids = set(
                    (
                        await session.scalars(
                            select(SourceAnchorRecord.anchor_id).where(
                                SourceAnchorRecord.anchor_id.in_(anchor_ids)
                            )
                        )
                    ).all()
                )
            else:
                known_anchor_ids = set()
            citation_by_id = {
                citation.citation_id: citation
                for citation in response.answer.citations
            }
            for order, claim in enumerate(response.claims, start=1):
                claim_id = f"{analysis_run_id}:{claim.claim_id or order}"
                session.add(
                    ClaimRecord(
                        claim_id=claim_id,
                        analysis_run_id=analysis_run_id,
                        claim_order=order,
                        claim_text=claim.claim_text,
                        claim_type=_claim_type(claim.claim_text),
                        importance="material",
                        status=claim.verdict,
                        confidence=claim.confidence,
                    )
                )
                citation = citation_by_id.get(claim.citation_id)
                if citation is None:
                    continue
                relation = "contradicts" if claim.verdict == "contradicted" else "supports"
                for anchor_id in citation.source_anchor_ids:
                    if anchor_id not in known_anchor_ids:
                        continue
                    session.add(
                        ClaimEvidenceRecord(
                            claim_evidence_id=f"{claim_id}:{anchor_id}",
                            claim_id=claim_id,
                            anchor_id=anchor_id,
                            relation=relation,
                            verifier_name="citation-verifier",
                            verifier_version=response.verification_version,
                            deterministic_checks_json=claim.deterministic_checks,
                            selected_for_output=True,
                        )
                    )
        return analysis_run_id

    async def get_run_evidence(self, analysis_run_id: str) -> dict[str, object] | None:
        """Return the persisted run, claims, and evidence links for audit views."""

        async with self._session_manager.session() as session:
            run = await session.get(AnalysisRunRecord, analysis_run_id)
            if run is None:
                return None
            claims = (
                await session.scalars(
                    select(ClaimRecord)
                    .where(ClaimRecord.analysis_run_id == analysis_run_id)
                    .order_by(ClaimRecord.claim_order)
                )
            ).all()
            claim_ids = [claim.claim_id for claim in claims]
            evidence = (
                await session.scalars(
                    select(ClaimEvidenceRecord).where(
                        ClaimEvidenceRecord.claim_id.in_(claim_ids)
                    )
                )
            ).all() if claim_ids else []
        return {
            "analysis_run": {
                "analysis_run_id": run.analysis_run_id,
                "session_id": run.session_id,
                "trace_id": run.trace_id,
                "workflow_id": run.workflow_id,
                "query": run.query,
                "status": run.status,
                "quality_summary": run.quality_summary_json,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            },
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "claim_order": claim.claim_order,
                    "claim_text": claim.claim_text,
                    "claim_type": claim.claim_type,
                    "status": claim.status,
                    "confidence": claim.confidence,
                }
                for claim in claims
            ],
            "claim_evidence": [
                {
                    "claim_evidence_id": item.claim_evidence_id,
                    "claim_id": item.claim_id,
                    "anchor_id": item.anchor_id,
                    "relation": item.relation,
                    "verifier_name": item.verifier_name,
                    "verifier_version": item.verifier_version,
                    "deterministic_checks": item.deterministic_checks_json,
                }
                for item in evidence
            ],
        }


def _claim_type(text: str) -> str:
    """Classify common high-risk claim forms without a model call."""

    lowered = text.lower()
    if any(token in lowered for token in ("%", "amount", "price", "cost", "months", "years")):
        return "numeric"
    if any(token in lowered for token in ("before", "after", "during", "date", "effective")):
        return "temporal"
    if any(token in lowered for token in ("more than", "less than", "compared", "versus")):
        return "comparative"
    return "factual"
