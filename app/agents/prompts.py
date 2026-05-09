"""Prompt templates for grounded answer generation and guarded fallbacks."""

from __future__ import annotations

from app.agents.schemas import ResearchGenerationRequest


def build_answer_instructions() -> str:
    """Return the shared answer-generation instruction block."""

    return (
        "You are CiteBot, a research assistant that must answer only from the provided "
        "contexts. Produce concise grounded answers, cite only supplied context items, "
        "state uncertainty when support is weak, and do not invent sources. Return JSON "
        "with direct_answer, supporting_evidence, citations, limitations, and "
        "follow_up_suggestion."
    )


def build_answer_prompt(request: ResearchGenerationRequest) -> str:
    """Render the current request, memory, and contexts into a model prompt."""

    context_lines: list[str] = []
    for index, context in enumerate(request.contexts, start=1):
        context_lines.append(
            f"[{index}] chunk_id={context.chunk_id} title={context.title!r} "
            f"source_uri={context.source_uri!r} location={context.location_marker!r} "
            f"source_type={context.source_type} text={context.text!r}"
        )
    tool_lines = [
        f"{record.tool_name}: {record.status} :: {record.output_summary}"
        for record in request.tool_calls
    ]
    return "\n".join(
        [
            f"Trace ID: {request.trace_id}",
            f"User query: {request.query}",
            f"Compressed memory: {request.memory.summary or 'None'}",
            (
                "Unresolved constraints: "
                f"{request.memory.unresolved_constraints or ['None']}"
            ),
            "Tool results:",
            *(tool_lines or ["None"]),
            "Contexts:",
            *(context_lines or ["None"]),
        ]
    )


def build_guarded_answer(query: str) -> str:
    """Return a controlled fallback answer for insufficiently supported queries."""

    return (
        f"I could not fully verify a grounded answer to: {query}. "
        "The available context is incomplete, so any response should be treated as partial."
    )
