"""LangGraph interrupt gate for review-required workflow products."""

from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.workflows.schemas import WorkProduct


class ReviewGateState(TypedDict, total=False):
    """Minimal state retained across an interrupt and resume."""

    work_product_id: str
    analysis_run_id: str
    status: str
    decision: str
    comment: str | None


class ReviewGate:
    """Pause review-required products and resume them using the analysis run ID."""

    def __init__(self, checkpointer: BaseCheckpointSaver) -> None:
        graph = StateGraph(ReviewGateState)
        graph.add_node("review_gate", self._review_gate)
        graph.add_edge(START, "review_gate")
        graph.add_edge("review_gate", END)
        self._graph = graph.compile(checkpointer=checkpointer)

    async def start(self, product: WorkProduct) -> dict[str, object]:
        """Start a gate; a review-required product intentionally raises GraphInterrupt."""

        config = {"configurable": {"thread_id": product.analysis_run_id}}
        return await self._graph.ainvoke(
            {
                "work_product_id": product.work_product_id,
                "analysis_run_id": product.analysis_run_id,
                "status": product.status,
            },
            config,
        )

    async def resume(
        self,
        analysis_run_id: str,
        decision: str,
        comment: str | None = None,
    ) -> dict[str, object]:
        """Resume an interrupted thread with an auditable reviewer decision."""

        config = {"configurable": {"thread_id": analysis_run_id}}
        result = await self._graph.ainvoke(
            Command(resume={"decision": decision, "comment": comment}),
            config,
        )
        return result

    async def restart(self, product: WorkProduct) -> dict[str, object]:
        """Start a fresh interrupt after a review edit kept the product reviewable."""

        checkpointer = self._graph.checkpointer
        if checkpointer is not None:
            await checkpointer.adelete_thread(product.analysis_run_id)
        return await self.start(product)

    async def _review_gate(self, state: ReviewGateState) -> ReviewGateState:
        """Interrupt only when the workflow policy requires human review."""

        if state.get("status") != "needs_review":
            return state
        payload = interrupt(
            {
                "type": "review_required",
                "work_product_id": state["work_product_id"],
                "analysis_run_id": state["analysis_run_id"],
                "reason": "Workflow policy requires explicit human approval.",
            }
        )
        if not isinstance(payload, dict):
            raise ValueError("Review resume payload must be an object")
        return {
            **state,
            "decision": str(payload.get("decision", "")),
            "comment": payload.get("comment"),
        }
