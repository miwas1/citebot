"""Conversation compression helpers that preserve recent turns and citation provenance."""

from __future__ import annotations

from collections import OrderedDict

from app.agents.schemas import ConversationTurn, ResearchMemory


def estimate_token_count(text: str) -> int:
    """Estimate token count with a conservative character-based heuristic."""

    if not text:
        return 0
    return max(1, len(text) // 4)


def build_research_memory(
    turns: list[ConversationTurn],
    recent_turns: int,
    summary_char_limit: int,
    preserve_citation_turns: int,
) -> ResearchMemory:
    """Build compressed memory while retaining recent turns and citation links."""

    retained_turns = turns[-recent_turns:]
    historical_turns = turns[:-recent_turns]
    summary = _summarize_turns(historical_turns, summary_char_limit)
    citation_graph = _build_citation_graph(turns[-preserve_citation_turns:])
    unresolved_constraints = _extract_unresolved_constraints(turns)
    return ResearchMemory(
        summary=summary,
        recent_turns=retained_turns,
        citation_graph=citation_graph,
        unresolved_constraints=unresolved_constraints,
    )


def _summarize_turns(turns: list[ConversationTurn], char_limit: int) -> str:
    """Create a compact deterministic summary of older conversation turns."""

    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns:
        prefix = "User" if turn.role == "user" else "Assistant"
        content = turn.content.strip().replace("\n", " ")
        if len(content) > 120:
            content = f"{content[:117].rstrip()}..."
        lines.append(f"{prefix}: {content}")
        candidate = " ".join(lines)
        if len(candidate) >= char_limit:
            break
    return " ".join(lines)[:char_limit].rstrip()


def _build_citation_graph(turns: list[ConversationTurn]) -> dict[str, list[str]]:
    """Collect citation-to-chunk relationships from assistant turns."""

    graph: OrderedDict[str, list[str]] = OrderedDict()
    for turn in turns:
        if turn.role != "assistant":
            continue
        for citation_id, chunk_id in zip(
            turn.citation_ids,
            turn.source_chunk_ids,
            strict=False,
        ):
            graph.setdefault(citation_id, [])
            if chunk_id not in graph[citation_id]:
                graph[citation_id].append(chunk_id)
    return dict(graph)


def _extract_unresolved_constraints(turns: list[ConversationTurn]) -> list[str]:
    """Retain user constraints that often matter on follow-up turns."""

    constraints: list[str] = []
    keywords = ("only", "must", "without", "latest", "cite", "citation")
    for turn in turns:
        if turn.role != "user":
            continue
        lowered = turn.content.lower()
        if any(keyword in lowered for keyword in keywords):
            trimmed = turn.content.strip()
            if trimmed not in constraints:
                constraints.append(trimmed)
    return constraints[-4:]
