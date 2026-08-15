"""Bounded answer refinement policy for failed claims."""

from __future__ import annotations

from app.agents.prompts import build_guarded_answer
from app.agents.schemas import ClaimVerification, ResearchAnswer


class AnswerRefiner:
    """Remove unsupported citations and qualify material verification failures once."""

    def refine(
        self,
        answer: ResearchAnswer,
        claims: list[ClaimVerification],
        query: str,
    ) -> ResearchAnswer:
        """Return a conservative answer without attempting open-ended regeneration."""

        failed_ids = {
            claim.citation_id
            for claim in claims
            if claim.verdict in {"unsupported", "contradicted", "insufficient", "stale"}
        }
        citations = [
            citation for citation in answer.citations if citation.citation_id not in failed_ids
        ]
        if not citations:
            direct_answer = build_guarded_answer(query)
            status = "insufficient_evidence"
        else:
            direct_answer = answer.direct_answer
            status = "needs_review" if any(
                claim.verdict in {"contradicted", "insufficient", "stale"}
                for claim in claims
            ) else "qualified"
        return answer.model_copy(
            update={
                "direct_answer": direct_answer,
                "citations": citations,
                "answer_status": status,
                "limitations": (
                    answer.limitations
                    or "Verification found claims requiring qualification or review."
                ),
            }
        )
