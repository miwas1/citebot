"""LangGraph-backed research agent orchestration for grounded answer workflows."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.compression import build_research_memory, estimate_token_count
from app.agents.generation import BaseAnswerGenerator
from app.agents.prompts import build_guarded_answer
from app.agents.schemas import (
    CitationVerificationResult,
    ConversationTurn,
    PythonSandboxExecution,
    ResearchAnswer,
    ResearchContext,
    ResearchGenerationRequest,
    ResearchMemory,
    ResearchQueryRequest,
    ResearchResponse,
    ResearchSessionRecord,
    RetrievalPlan,
    ToolCallRecord,
    create_session_id,
    create_trace_id,
)
from app.agents.session_store import ResearchSessionStore
from app.core.config import Settings
from app.evidence.repository import EvidenceLedgerRepository
from app.evidence.schemas import AtomicClaim, EvidenceCandidate, VerificationDecision
from app.evidence.service import EvidenceService
from app.ingestion.schemas import SearchRequest, SearchResult
from app.retrieval.query_planner import decompose_query
from app.retrieval.service import RetrievalService
from app.tools.citation_verifier import CitationVerifier
from app.tools.python_sandbox import PythonSandboxTool
from app.tools.web_search import BaseWebSearchTool

logger = logging.getLogger(__name__)


class ResearchAgentState(TypedDict, total=False):
    """Runtime state persisted across LangGraph node transitions."""

    session_id: str
    trace_id: str
    request: ResearchQueryRequest
    query: str
    rewritten_query: str
    conversation_history: list[ConversationTurn]
    memory: ResearchMemory
    retrieval_plan: RetrievalPlan
    retrieved_contexts: list[ResearchContext]
    tool_calls: list[ToolCallRecord]
    draft_answer: ResearchAnswer
    verification: CitationVerificationResult
    final_response: ResearchResponse
    token_usage: dict[str, int]
    state_transitions: list[str]
    error: str
    session_turns: list[ConversationTurn]
    atomic_claims: list[AtomicClaim]
    evidence_candidates: dict[str, list[EvidenceCandidate]]
    verification_decisions: list[VerificationDecision]
    stage_timings_ms: dict[str, float]


class ResearchAgentService:
    """Coordinate retrieval, tools, generation, and verification through LangGraph."""

    def __init__(
        self,
        settings: Settings,
        retrieval_service: RetrievalService,
        answer_generator: BaseAnswerGenerator,
        web_search_tool: BaseWebSearchTool,
        python_sandbox: PythonSandboxTool,
        citation_verifier: CitationVerifier,
        session_store: ResearchSessionStore,
        evidence_repository: EvidenceLedgerRepository | None = None,
        evidence_service: EvidenceService | None = None,
    ) -> None:
        """Store dependencies and compile the research workflow graph."""

        self._settings = settings
        self._retrieval_service = retrieval_service
        self._answer_generator = answer_generator
        self._web_search_tool = web_search_tool
        self._python_sandbox = python_sandbox
        self._citation_verifier = citation_verifier
        self._session_store = session_store
        self._evidence_repository = evidence_repository
        self._evidence_service = evidence_service or EvidenceService()
        self._graph = self._build_graph().compile()

    async def answer(
        self,
        request: ResearchQueryRequest,
        trace_id: str | None = None,
    ) -> ResearchResponse:
        """Run the research graph for one request and persist updated session state."""

        session_id = request.session_id or create_session_id()
        trace_id = trace_id or create_trace_id()
        stored_session = await self._session_store.get(session_id)
        initial_state: ResearchAgentState = {
            "session_id": session_id,
            "trace_id": trace_id,
            "request": request,
            "conversation_history": stored_session.turns if stored_session else [],
            "memory": stored_session.memory if stored_session else ResearchMemory(),
            "retrieved_contexts": [],
            "tool_calls": [],
            "token_usage": {},
            "state_transitions": [],
        }
        result = await self._graph.ainvoke(initial_state)
        response = result.get("final_response")
        if response is None:
            response = self._build_error_response(
                session_id=session_id,
                trace_id=trace_id,
                error_message=result.get("error", "Unknown research agent failure."),
                memory=result.get("memory", ResearchMemory()),
                state_transitions=result.get("state_transitions", []),
            )
        await self._persist_session(
            session_id=session_id,
            response=response,
            session_turns=result.get(
                "session_turns",
                stored_session.turns if stored_session else [],
            ),
        )
        if self._evidence_repository is not None:
            try:
                await self._evidence_repository.save_response(response, request.query)
            except Exception:
                logger.exception("evidence_ledger_persist_failed trace_id=%s", trace_id)
        logger.info(
            "research_trace=%s session_id=%s transitions=%s",
            trace_id,
            session_id,
            response.state_transitions,
        )
        return response

    def _build_graph(self) -> StateGraph:
        """Construct the LangGraph state machine for research workflows."""

        graph = StateGraph(ResearchAgentState)
        nodes = {
            "validate_input": self._validate_input,
            "classify_query": self._classify_query,
            "rewrite_query": self._rewrite_query,
            "plan_retrieval": self._plan_retrieval,
            "hybrid_retrieval": self._hybrid_retrieval,
            "web_search": self._web_search,
            "python_analysis": self._python_analysis,
            "answer_generation": self._answer_generation,
            "claim_extraction": self._claim_extraction,
            "evidence_selection": self._evidence_selection,
            "deterministic_verification": self._deterministic_verification,
            "citation_verification": self._citation_verification,
            "guarded_response": self._guarded_response,
            "final_response_formatting": self._final_response_formatting,
            "error_handler": self._error_handler,
        }
        for name, node in nodes.items():
            graph.add_node(name, self._timed_node(name, node))
        graph.add_edge(START, "validate_input")
        graph.add_conditional_edges(
            "validate_input",
            self._route_after_validation,
            ["classify_query", "error_handler"],
        )
        graph.add_edge("classify_query", "rewrite_query")
        graph.add_edge("rewrite_query", "plan_retrieval")
        graph.add_edge("plan_retrieval", "hybrid_retrieval")
        graph.add_conditional_edges(
            "hybrid_retrieval",
            self._route_after_retrieval,
            ["web_search", "python_analysis", "answer_generation", "error_handler"],
        )
        graph.add_conditional_edges(
            "web_search",
            self._route_after_web_search,
            ["python_analysis", "answer_generation"],
        )
        graph.add_edge("python_analysis", "answer_generation")
        graph.add_edge("answer_generation", "claim_extraction")
        graph.add_edge("claim_extraction", "evidence_selection")
        graph.add_edge("evidence_selection", "deterministic_verification")
        graph.add_edge("deterministic_verification", "citation_verification")
        graph.add_conditional_edges(
            "citation_verification",
            self._route_after_verification,
            ["guarded_response", "final_response_formatting"],
        )
        graph.add_edge("guarded_response", "final_response_formatting")
        graph.add_edge("final_response_formatting", END)
        graph.add_edge("error_handler", END)
        return graph

    async def _validate_input(self, state: ResearchAgentState) -> ResearchAgentState:
        """Reject empty queries and initialize the tracked query string."""

        request = state["request"]
        query = request.query.strip()
        if not query:
            return {
                "error": "Query cannot be empty.",
                "state_transitions": _append_transition(state, "validate_input"),
            }
        return {
            "query": query,
            "state_transitions": _append_transition(state, "validate_input"),
            "token_usage": _with_token_usage(state, "input_validation", query),
        }

    async def _classify_query(self, state: ResearchAgentState) -> ResearchAgentState:
        """Classify whether freshness or computation signals are present."""

        request = state["request"]
        query = state["query"]
        reason_codes: list[str] = []
        lowered = query.lower()
        if request.freshness_required or any(
            token in lowered for token in ("latest", "recent", "today", "current")
        ):
            reason_codes.append("freshness_required")
        if any(token in lowered for token in ("web", "internet", "online")):
            reason_codes.append("user_requested_web")
        if request.analysis_code:
            reason_codes.append("computation_requested")
        return {
            "retrieval_plan": RetrievalPlan(
                retrieval_required=True,
                top_k=request.top_k or self._settings.research_top_k,
                reason_codes=reason_codes,
            ),
            "state_transitions": _append_transition(state, "classify_query"),
            "token_usage": _with_token_usage(state, "query_classification", query),
        }

    async def _rewrite_query(self, state: ResearchAgentState) -> ResearchAgentState:
        """Normalize the query while preserving the original user intent."""

        query = " ".join(state["query"].split())
        return {
            "rewritten_query": query,
            "state_transitions": _append_transition(state, "query_rewriting"),
            "token_usage": _with_token_usage(state, "query_rewriting", query),
        }

    async def _plan_retrieval(self, state: ResearchAgentState) -> ResearchAgentState:
        """Convert classification signals into concrete retrieval and tool policy."""

        request = state["request"]
        plan = state["retrieval_plan"]
        allow_web_search = (
            self._settings.allow_web_search_default
            if request.allow_web_search is None
            else request.allow_web_search
        )
        allow_python = (
            self._settings.allow_python_execution_default
            if request.allow_python_execution is None
            else request.allow_python_execution
        )
        use_web_search = allow_web_search and any(
            code in plan.reason_codes
            for code in ("freshness_required", "user_requested_web")
        )
        use_python = allow_python and bool(request.analysis_code)
        return {
            "retrieval_plan": plan.model_copy(
                update={
                    "top_k": request.top_k or self._settings.research_top_k,
                    "use_web_search": use_web_search,
                    "use_python": use_python,
                    "decomposed_queries": decompose_query(state["rewritten_query"]),
                }
            ),
            "state_transitions": _append_transition(state, "retrieval_planning"),
        }

    async def _hybrid_retrieval(self, state: ResearchAgentState) -> ResearchAgentState:
        """Run hybrid retrieval and normalize results into research contexts."""

        plan = state["retrieval_plan"]
        try:
            queries = plan.decomposed_queries or [state["rewritten_query"]]
            result_batches = [
                await self._retrieval_service.explain(
                    SearchRequest(
                        query=query,
                        top_k=plan.top_k,
                        strategy="hybrid",
                        include_explain=True,
                    )
                )
                for query in queries[:4]
            ]
            results = _merge_search_results(result_batches)
        except Exception as error:
            return {
                "error": f"Retrieval failed: {error}",
                "state_transitions": _append_transition(state, "hybrid_retrieval"),
            }
        contexts = [self._context_from_result(result) for result in results]
        insufficient_context = not contexts or (
            contexts[0].score < self._settings.research_min_context_score
        )
        return {
            "retrieved_contexts": contexts,
            "retrieval_plan": plan.model_copy(
                update={"insufficient_context": insufficient_context}
            ),
            "state_transitions": _append_transition(state, "hybrid_retrieval"),
            "token_usage": _with_token_usage(
                state,
                "hybrid_retrieval",
                *(context.text for context in contexts),
            ),
        }

    async def _web_search(self, state: ResearchAgentState) -> ResearchAgentState:
        """Optionally enrich insufficient internal context with Tavily results."""

        contexts, record = await self._web_search_tool.search(
            state["rewritten_query"],
            state["trace_id"],
        )
        combined_contexts = state.get("retrieved_contexts", []) + contexts
        return {
            "retrieved_contexts": combined_contexts,
            "tool_calls": state.get("tool_calls", []) + [record],
            "state_transitions": _append_transition(state, "web_search_escalation"),
            "token_usage": _with_token_usage(
                state,
                "web_search",
                *(context.text for context in contexts),
            ),
        }

    async def _python_analysis(self, state: ResearchAgentState) -> ResearchAgentState:
        """Run explicit user-provided analysis code inside the local sandbox."""

        request = state["request"]
        if not request.analysis_code:
            return {
                "state_transitions": _append_transition(
                    state, "python_analysis_escalation"
                ),
            }
        result, record = await self._python_sandbox.execute(
            PythonSandboxExecution(
                code=request.analysis_code,
                inputs=request.analysis_inputs,
                trace_id=state["trace_id"],
            )
        )
        analysis_contexts = list(state.get("retrieved_contexts", []))
        if result.stdout or result.result_json:
            analysis_contexts.append(
                ResearchContext(
                    chunk_id=f"analysis:{state['trace_id']}",
                    document_id=f"analysis:{state['trace_id']}",
                    title="Python analysis output",
                    source_uri="python://sandbox",
                    text=result.stdout or str(result.result_json),
                    score=1.0,
                    source_backend="sandbox",
                    source_type="analysis",
                )
            )
        return {
            "retrieved_contexts": analysis_contexts,
            "tool_calls": state.get("tool_calls", []) + [record],
            "state_transitions": _append_transition(
                state, "python_analysis_escalation"
            ),
            "token_usage": _with_token_usage(
                state,
                "python_analysis",
                result.stdout,
                str(result.result_json),
            ),
        }

    async def _answer_generation(self, state: ResearchAgentState) -> ResearchAgentState:
        """Generate a grounded answer from the accumulated contexts and tool outputs."""

        answer = await self._answer_generator.generate(
            ResearchGenerationRequest(
                query=state["query"],
                trace_id=state["trace_id"],
                memory=state.get("memory", ResearchMemory()),
                contexts=state.get("retrieved_contexts", []),
                tool_calls=state.get("tool_calls", []),
            )
        )
        return {
            "draft_answer": answer,
            "state_transitions": _append_transition(state, "answer_generation"),
            "token_usage": _with_token_usage(
                state,
                "answer_generation",
                answer.direct_answer,
                *answer.supporting_evidence,
            ),
        }

    async def _citation_verification(
        self, state: ResearchAgentState
    ) -> ResearchAgentState:
        """Verify answer citations against the current retrieved context set."""

        verification = await self._citation_verifier.verify(
            state["draft_answer"],
            state.get("retrieved_contexts", []),
        )
        verification = self._evidence_service.apply_decisions(
            verification,
            state.get("verification_decisions", []),
        )
        return {
            "verification": verification,
            "state_transitions": _append_transition(state, "citation_verification"),
        }

    async def _claim_extraction(self, state: ResearchAgentState) -> ResearchAgentState:
        """Extract atomic claims before any evidence verdict is produced."""

        claims = self._evidence_service.extract(state["draft_answer"])
        return {
            "atomic_claims": claims,
            "state_transitions": _append_transition(state, "claim_extraction"),
        }

    async def _evidence_selection(self, state: ResearchAgentState) -> ResearchAgentState:
        """Select bounded candidates from the contexts already retrieved."""

        candidates = self._evidence_service.select(
            state.get("atomic_claims", []),
            state["draft_answer"],
            state.get("retrieved_contexts", []),
        )
        return {
            "evidence_candidates": candidates,
            "state_transitions": _append_transition(state, "evidence_selection"),
        }

    async def _deterministic_verification(
        self, state: ResearchAgentState
    ) -> ResearchAgentState:
        """Run low-cost deterministic checks before optional NLI verification."""

        decisions = self._evidence_service.verify(
            state.get("atomic_claims", []),
            state.get("evidence_candidates", {}),
        )
        return {
            "verification_decisions": decisions,
            "state_transitions": _append_transition(state, "deterministic_verification"),
        }

    async def _guarded_response(self, state: ResearchAgentState) -> ResearchAgentState:
        """Qualify or reduce answers when verification finds unsupported citations."""

        answer = state["draft_answer"]
        verification = state["verification"]
        guarded_answer = self._evidence_service.refiner.refine(
            answer,
            verification.claims,
            state["query"],
        ).model_copy(
            update={
                "limitations": (
                    "Verification found unsupported or weakly supported claims. "
                    f"Overall verdict: {verification.overall_verdict}."
                ),
                "follow_up_suggestion": (
                    answer.follow_up_suggestion
                    or "Refine the question or expand the available corpus for stronger support."
                ),
            }
        )
        return {
            "draft_answer": guarded_answer,
            "state_transitions": _append_transition(state, "guarded_response"),
        }

    async def _final_response_formatting(
        self,
        state: ResearchAgentState,
    ) -> ResearchAgentState:
        """Assemble the final API response and the persisted session turn history."""

        answer = state["draft_answer"]
        verification = state["verification"]
        if verification.overall_verdict == "supported":
            answer = answer.model_copy(update={"answer_status": "supported"})
        elif answer.answer_status == "draft":
            answer = answer.model_copy(update={"answer_status": "qualified"})
        user_turn = ConversationTurn(
            role="user",
            content=state["query"],
            trace_id=state["trace_id"],
        )
        assistant_turn = ConversationTurn(
            role="assistant",
            content=answer.direct_answer,
            citation_ids=[citation.citation_id for citation in answer.citations],
            source_chunk_ids=[citation.chunk_id for citation in answer.citations],
            trace_id=state["trace_id"],
        )
        session_turns = state.get("conversation_history", []) + [
            user_turn,
            assistant_turn,
        ]
        memory = build_research_memory(
            turns=session_turns,
            recent_turns=self._settings.research_recent_turns,
            summary_char_limit=self._settings.research_summary_char_limit,
            preserve_citation_turns=self._settings.research_preserve_citation_turns,
        )
        response = ResearchResponse(
            session_id=state["session_id"],
            trace_id=state["trace_id"],
            answer=answer,
            verification=verification,
            memory=memory,
            tool_calls=state.get("tool_calls", []),
            token_usage=state.get("token_usage", {}),
            state_transitions=_append_transition(state, "final_response_formatting"),
            retrieved_contexts=state.get("retrieved_contexts", []),
            claims=verification.claims,
            evidence_coverage=verification.evidence_coverage,
            contradiction_count=verification.contradiction_count,
            verification_version=verification.verification_version,
            stage_timings_ms=state.get("stage_timings_ms", {}),
        )
        return {
            "memory": memory,
            "session_turns": session_turns,
            "final_response": response,
            "state_transitions": response.state_transitions,
        }

    async def _error_handler(self, state: ResearchAgentState) -> ResearchAgentState:
        """Return a controlled error response that still carries a trace identifier."""

        response = self._build_error_response(
            session_id=state["session_id"],
            trace_id=state["trace_id"],
            error_message=state.get("error", "Unhandled research agent error."),
            memory=state.get("memory", ResearchMemory()),
            state_transitions=_append_transition(state, "error_handler"),
            stage_timings_ms=state.get("stage_timings_ms", {}),
        )
        return {
            "final_response": response,
            "state_transitions": response.state_transitions,
        }

    async def _route_after_validation(
        self,
        state: ResearchAgentState,
    ) -> Literal["classify_query", "error_handler"]:
        """Route to graph execution or the controlled error path."""

        return "error_handler" if state.get("error") else "classify_query"

    async def _route_after_retrieval(
        self,
        state: ResearchAgentState,
    ) -> Literal["web_search", "python_analysis", "answer_generation", "error_handler"]:
        """Route retrieval results through web search, computation, or generation."""

        if state.get("error"):
            return "error_handler"
        plan = state["retrieval_plan"]
        if plan.use_web_search and plan.insufficient_context:
            return "web_search"
        if plan.use_python:
            return "python_analysis"
        return "answer_generation"

    async def _route_after_web_search(
        self,
        state: ResearchAgentState,
    ) -> Literal["python_analysis", "answer_generation"]:
        """Route post-web-search flow toward computation or final generation."""

        return (
            "python_analysis"
            if state["retrieval_plan"].use_python
            else "answer_generation"
        )

    async def _route_after_verification(
        self,
        state: ResearchAgentState,
    ) -> Literal["guarded_response", "final_response_formatting"]:
        """Route verified answers to output or guarded revision."""

        if state["verification"].overall_verdict == "supported":
            return "final_response_formatting"
        return "guarded_response"

    def _context_from_result(self, result: SearchResult) -> ResearchContext:
        """Convert a retrieval result into a context object for generation."""

        text = result.text.strip()
        if len(text) > self._settings.research_context_char_limit:
            text = (
                f"{text[: self._settings.research_context_char_limit - 3].rstrip()}..."
            )
        return ResearchContext(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            title=result.title,
            source_uri=result.source_uri,
            location_marker=result.location_marker,
            text=text,
            score=result.score,
            source_backend=result.source_backend,
            source_type="internal",
            metadata=result.metadata,
        )

    async def _persist_session(
        self,
        session_id: str,
        response: ResearchResponse,
        session_turns: list[ConversationTurn],
    ) -> None:
        """Store the latest session state for follow-up turns and replay."""

        await self._session_store.save(
            ResearchSessionRecord(
                session_id=session_id,
                turns=session_turns,
                memory=response.memory,
                last_trace_id=response.trace_id,
            )
        )

    def _build_error_response(
        self,
        session_id: str,
        trace_id: str,
        error_message: str,
        memory: ResearchMemory,
        state_transitions: list[str],
        stage_timings_ms: dict[str, float] | None = None,
    ) -> ResearchResponse:
        """Construct a controlled failure payload for API consumers."""

        return ResearchResponse(
            session_id=session_id,
            trace_id=trace_id,
            answer=ResearchAnswer(
                direct_answer=build_guarded_answer("the requested research question"),
                limitations=error_message,
            ),
            verification=CitationVerificationResult(overall_verdict="unsupported"),
            memory=memory,
            token_usage={},
            state_transitions=state_transitions,
            stage_timings_ms=stage_timings_ms or {},
            error=error_message,
        )

    def _timed_node(
        self,
        name: str,
        node: Callable[[ResearchAgentState], Awaitable[ResearchAgentState]],
    ) -> Callable[[ResearchAgentState], Awaitable[ResearchAgentState]]:
        """Wrap one graph node with bounded stage timing telemetry."""

        async def run(state: ResearchAgentState) -> ResearchAgentState:
            started = perf_counter()
            result = await node(state)
            timings = dict(state.get("stage_timings_ms", {}))
            timings.update(result.get("stage_timings_ms", {}))
            timings[name] = round((perf_counter() - started) * 1000, 3)
            result["stage_timings_ms"] = timings
            final_response = result.get("final_response")
            if final_response is not None:
                result["final_response"] = final_response.model_copy(
                    update={"stage_timings_ms": timings}
                )
            return result

        return run


def _append_transition(state: ResearchAgentState, name: str) -> list[str]:
    """Append one state transition name to the execution trace."""

    return state.get("state_transitions", []) + [name]


def _with_token_usage(
    state: ResearchAgentState,
    stage: str,
    *texts: str,
) -> dict[str, int]:
    """Update approximate token accounting for a single graph stage."""

    token_usage = dict(state.get("token_usage", {}))
    token_usage[stage] = sum(estimate_token_count(text) for text in texts if text)
    return token_usage


def _merge_search_results(result_batches: list[list[SearchResult]]) -> list[SearchResult]:
    """Merge decomposed-query results by best score without duplicating chunks."""

    merged: dict[str, SearchResult] = {}
    for batch in result_batches:
        for result in batch:
            existing = merged.get(result.chunk_id)
            if existing is None or result.score > existing.score:
                merged[result.chunk_id] = result
    return sorted(merged.values(), key=lambda result: result.score, reverse=True)
