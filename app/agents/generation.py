"""Answer generation adapters with deterministic local and vendor-backed modes."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import httpx

from app.agents.prompts import build_answer_instructions, build_answer_prompt
from app.agents.schemas import (
    Citation,
    ResearchAnswer,
    ResearchContext,
    ResearchGenerationRequest,
)
from app.core.config import Settings


class BaseAnswerGenerator(ABC):
    """Interface for generating grounded answers from retrieved contexts."""

    @abstractmethod
    async def generate(self, request: ResearchGenerationRequest) -> ResearchAnswer:
        """Return a structured grounded answer for the supplied request."""


class LocalAnswerGenerator(BaseAnswerGenerator):
    """Deterministic local answer generator used for tests and offline development."""

    async def generate(self, request: ResearchGenerationRequest) -> ResearchAnswer:
        """Generate an extractive answer from the highest-ranked contexts."""

        return _build_answer_from_contexts(request.contexts, request.query)


class OpenAIAnswerGenerator(BaseAnswerGenerator):
    """Responses API-backed answer generator for OpenAI models."""

    def __init__(self, api_key: str, model: str) -> None:
        """Store credentials and model configuration for generation calls."""

        self._api_key = api_key
        self._model = model

    async def generate(self, request: ResearchGenerationRequest) -> ResearchAnswer:
        """Generate an answer through the OpenAI Responses API with JSON output."""

        payload = {
            "model": self._model,
            "instructions": build_answer_instructions(),
            "input": build_answer_prompt(request),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return _build_answer_from_contexts(request.contexts, request.query)
        data = response.json()
        output_text = data.get("output_text") or _extract_openai_output_text(data)
        return _parse_answer_json(output_text, request.contexts, request.query)


class GeminiAnswerGenerator(BaseAnswerGenerator):
    """Gemini generateContent-backed answer generator for text synthesis."""

    def __init__(self, api_key: str, model: str) -> None:
        """Store credentials and model configuration for generation calls."""

        self._api_key = api_key
        self._model = model

    async def generate(self, request: ResearchGenerationRequest) -> ResearchAnswer:
        """Generate an answer through the Gemini generateContent endpoint."""

        payload = {
            "systemInstruction": {
                "parts": [{"text": build_answer_instructions()}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": build_answer_prompt(request)}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent?key={self._api_key}"
        )
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except httpx.HTTPError:
            return _build_answer_from_contexts(request.contexts, request.query)
        data = response.json()
        output_text = _extract_gemini_output_text(data)
        return _parse_answer_json(output_text, request.contexts, request.query)


def build_answer_generator(settings: Settings) -> BaseAnswerGenerator:
    """Build the configured answer generator with safe local fallback behavior."""

    if settings.answer_provider == "local":
        return LocalAnswerGenerator()
    if settings.answer_provider == "openai":
        if not settings.openai_api_key:
            msg = "OPENAI_API_KEY is required when ANSWER_PROVIDER=openai"
            raise ValueError(msg)
        return OpenAIAnswerGenerator(settings.openai_api_key, settings.answer_model)
    if settings.answer_provider == "gemini":
        if not settings.gemini_api_key:
            msg = "GEMINI_API_KEY is required when ANSWER_PROVIDER=gemini"
            raise ValueError(msg)
        return GeminiAnswerGenerator(
            settings.gemini_api_key,
            settings.gemini_answer_model,
        )
    msg = "ANSWER_PROVIDER must be one of local, openai, or gemini"
    raise ValueError(msg)


def _parse_answer_json(
    output_text: str,
    contexts: list[ResearchContext],
    query: str,
) -> ResearchAnswer:
    """Parse model JSON output, falling back to deterministic synthesis on error."""

    if not output_text:
        return _build_answer_from_contexts(contexts, query)
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError:
        return _build_answer_from_contexts(contexts, query)
    fallback = _build_answer_from_contexts(contexts, query)
    citations = _build_citations(contexts)
    return ResearchAnswer(
        direct_answer=payload.get("direct_answer") or fallback.direct_answer,
        supporting_evidence=payload.get("supporting_evidence")
        or fallback.supporting_evidence,
        citations=citations or fallback.citations,
        limitations=payload.get("limitations") or fallback.limitations,
        follow_up_suggestion=payload.get("follow_up_suggestion")
        or fallback.follow_up_suggestion,
    )


def _build_answer_from_contexts(
    contexts: list[ResearchContext],
    query: str,
) -> ResearchAnswer:
    """Construct a concise grounded answer directly from ranked contexts."""

    if not contexts:
        return ResearchAnswer(
            direct_answer=(
                f"I do not have enough grounded context to answer: {query}."
            ),
            limitations="No retrieved context satisfied the query.",
            follow_up_suggestion="Refine the query or allow web search if freshness matters.",
        )
    citations = _build_citations(contexts)
    primary_context = contexts[0]
    evidence = [_support_span(context) for context in contexts[:3]]
    answer_text = (
        f"Based on the retrieved sources, {primary_context.text.strip().rstrip('.')}."
    )
    limitation = None
    if len(contexts) == 1:
        limitation = "The answer is grounded in a single retrieved context."
    return ResearchAnswer(
        direct_answer=answer_text,
        supporting_evidence=evidence,
        citations=citations,
        limitations=limitation,
    )


def _build_citations(contexts: list[ResearchContext]) -> list[Citation]:
    """Create citations from the ranked contexts with stable citation IDs."""

    citations: list[Citation] = []
    for index, context in enumerate(contexts[:3], start=1):
        support = _support_span(context)
        citations.append(
            Citation(
                citation_id=f"C{index}",
                chunk_id=context.chunk_id,
                document_id=context.document_id,
                title=context.title,
                source_uri=context.source_uri,
                location_marker=context.location_marker,
                source_type=context.source_type,
                support_span=support,
                quoted_support=support,
                fetched_at=context.fetched_at,
            )
        )
    return citations


def _support_span(context: ResearchContext) -> str:
    """Return a stable short support span for citation display and verification."""

    text = context.text.strip().replace("\n", " ")
    if len(text) <= 180:
        return text
    return f"{text[:177].rstrip()}..."


def _extract_openai_output_text(payload: dict[str, object]) -> str:
    """Aggregate text content from a Responses API payload."""

    outputs = payload.get("output")
    if not isinstance(outputs, list):
        return ""
    collected: list[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(
                content.get("text"),
                str,
            ):
                collected.append(content["text"])
    return "\n".join(collected)


def _extract_gemini_output_text(payload: dict[str, object]) -> str:
    """Extract text from the first Gemini candidate response."""

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    first_candidate = candidates[0]
    if not isinstance(first_candidate, dict):
        return ""
    content = first_candidate.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    return "\n".join(text for text in texts if text)
