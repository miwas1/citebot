"""Answer generation adapters with deterministic local and vendor-backed modes."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from time import monotonic

import httpx

from app.agents.prompts import build_answer_instructions, build_answer_prompt
from app.agents.schemas import (
    Citation,
    ResearchAnswer,
    ResearchContext,
    ResearchGenerationRequest,
)
from app.core.config import Settings
from app.observability.metrics import InMemoryMetricsRegistry, LlmCallMetrics

logger = logging.getLogger(__name__)


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


class LlamaCppAnswerGenerator(BaseAnswerGenerator):
    """Generate grounded JSON answers through a local llama.cpp server."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        concurrency: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
        metrics_registry: InMemoryMetricsRegistry | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._transport = transport
        self._metrics_registry = metrics_registry

    async def generate(self, request: ResearchGenerationRequest) -> ResearchAnswer:
        """Generate one answer while enforcing the local model concurrency budget."""

        endpoint = self._base_url
        if not endpoint.endswith("/v1"):
            endpoint = f"{endpoint}/v1"
        endpoint = f"{endpoint}/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": build_answer_instructions()},
                {"role": "user", "content": build_answer_prompt(request)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "timings_per_token": True,
        }
        started = monotonic()
        queue_wait_ms = 0.0
        http_ms = 0.0
        http_started: float | None = None
        try:
            async with self._semaphore:
                queue_wait_ms = (monotonic() - started) * 1000
                http_started = monotonic()
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    transport=self._transport,
                ) as client:
                    response = await client.post(endpoint, json=payload)
                    response.raise_for_status()
                http_ms = (monotonic() - http_started) * 1000
        except httpx.HTTPError as error:
            if http_started is not None:
                http_ms = (monotonic() - http_started) * 1000
            self._record_call(
                request=request,
                status="error",
                started=started,
                queue_wait_ms=queue_wait_ms,
                http_ms=http_ms,
            )
            raise RuntimeError(f"Local answer service unavailable: {error}") from error
        response_payload = response.json()
        self._record_call(
            request=request,
            status="ok",
            started=started,
            queue_wait_ms=queue_wait_ms,
            http_ms=http_ms,
            response_payload=response_payload,
        )
        output_text = _extract_chat_completion_text(response_payload)
        return _parse_answer_json(output_text, request.contexts, request.query)

    def _record_call(
        self,
        request: ResearchGenerationRequest,
        status: str,
        started: float,
        queue_wait_ms: float,
        http_ms: float,
        response_payload: dict[str, object] | None = None,
    ) -> None:
        """Record one trace-linked model call without storing prompt content."""

        timing = _llama_cpp_timing(response_payload or {}, http_ms=http_ms)
        total_ms = (monotonic() - started) * 1000
        metrics = LlmCallMetrics(
            provider="llama-cpp",
            model=self._model,
            trace_id=request.trace_id,
            status=status,
            total_ms=total_ms,
            queue_wait_ms=queue_wait_ms,
            http_ms=http_ms,
            **timing,
        )
        if self._metrics_registry is not None:
            self._metrics_registry.record_llm_call(metrics)
        logger.info(
            "event=llm_call_completed provider=%s model=%s status=%s trace_id=%s "
            "total_ms=%.2f queue_wait_ms=%.2f http_ms=%.2f prompt_ms=%s "
            "generation_ms=%s overhead_ms=%s prompt_tokens=%s completion_tokens=%s "
            "prompt_tokens_per_second=%s completion_tokens_per_second=%s",
            metrics.provider,
            metrics.model,
            metrics.status,
            metrics.trace_id,
            metrics.total_ms,
            metrics.queue_wait_ms,
            metrics.http_ms,
            metrics.prompt_ms,
            metrics.generation_ms,
            metrics.overhead_ms,
            metrics.prompt_tokens,
            metrics.completion_tokens,
            metrics.prompt_tokens_per_second,
            metrics.completion_tokens_per_second,
        )


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


def build_answer_generator(
    settings: Settings,
    metrics_registry: InMemoryMetricsRegistry | None = None,
) -> BaseAnswerGenerator:
    """Build the configured answer generator with safe local fallback behavior."""

    if settings.answer_provider == "llama-cpp":
        return LlamaCppAnswerGenerator(
            base_url=settings.llm_base_url,
            model=settings.answer_model,
            timeout_seconds=settings.llm_timeout_seconds,
            concurrency=settings.llm_generation_concurrency,
            metrics_registry=metrics_registry,
        )
    if settings.answer_provider in {"local", "test"}:
        return LocalAnswerGenerator()
    if settings.answer_provider == "openai":
        if settings.runtime_mode == "offline":
            raise ValueError("OpenAI answers are disabled in RUNTIME_MODE=offline")
        if not settings.openai_api_key:
            msg = "OPENAI_API_KEY is required when ANSWER_PROVIDER=openai"
            raise ValueError(msg)
        return OpenAIAnswerGenerator(settings.openai_api_key, settings.answer_model)
    if settings.answer_provider == "gemini":
        if settings.runtime_mode == "offline":
            raise ValueError("Gemini answers are disabled in RUNTIME_MODE=offline")
        if not settings.gemini_api_key:
            msg = "GEMINI_API_KEY is required when ANSWER_PROVIDER=gemini"
            raise ValueError(msg)
        return GeminiAnswerGenerator(
            settings.gemini_api_key,
            settings.gemini_answer_model,
        )
    msg = "ANSWER_PROVIDER must be one of llama-cpp, local, test, openai, or gemini"
    raise ValueError(msg)


def _llama_cpp_timing(
    payload: dict[str, object],
    http_ms: float,
) -> dict[str, object]:
    """Normalize llama.cpp timing and OpenAI-compatible usage fields."""

    raw_timings = payload.get("timings")
    timings = raw_timings if isinstance(raw_timings, dict) else {}
    raw_usage = payload.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_ms = _number(timings.get("prompt_ms"))
    generation_ms = _number(timings.get("predicted_ms"))
    has_server_timing = prompt_ms is not None or generation_ms is not None
    measured_ms = sum(value for value in (prompt_ms, generation_ms) if value is not None)
    return {
        "prompt_ms": prompt_ms,
        "generation_ms": generation_ms,
        "overhead_ms": max(0.0, http_ms - measured_ms) if has_server_timing else None,
        "prompt_tokens": _integer(
            timings.get("prompt_n"), usage.get("prompt_tokens")
        ),
        "completion_tokens": _integer(
            timings.get("predicted_n"), usage.get("completion_tokens")
        ),
        "prompt_tokens_per_second": _number(timings.get("prompt_per_second")),
        "completion_tokens_per_second": _number(
            timings.get("predicted_per_second")
        ),
    }


def _number(value: object) -> float | None:
    """Return a finite-looking numeric response value as a float."""

    return float(value) if isinstance(value, int | float) else None


def _integer(*values: object) -> int | None:
    """Return the first integer-like response value."""

    for value in values:
        if isinstance(value, int):
            return value
    return None


def _extract_chat_completion_text(payload: dict[str, object]) -> str:
    """Extract content from an OpenAI-compatible chat completion response."""

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        return content if isinstance(content, str) else ""
    return ""


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
                page=context.metadata.get("page"),
                element_ids=list(context.metadata.get("element_ids") or []),
                source_anchor_ids=list(context.metadata.get("source_anchor_ids") or []),
                bbox_refs=list(context.metadata.get("bbox_refs") or []),
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
