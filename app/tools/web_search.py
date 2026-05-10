"""Web search adapters for policy-gated Tavily-backed research enrichment."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic

import httpx

from app.agents.schemas import ResearchContext, ToolCallRecord
from app.core.config import Settings


class BaseWebSearchTool:
    """Interface for external web search enrichments."""

    async def search(
        self,
        query: str,
        trace_id: str,
    ) -> tuple[list[ResearchContext], ToolCallRecord]:
        """Return normalized contexts plus the associated tool audit record."""

        raise NotImplementedError


class TavilyWebSearchTool(BaseWebSearchTool):
    """HTTP adapter for Tavily search with timeout and retry handling."""

    def __init__(self, settings: Settings) -> None:
        """Store the Tavily request configuration."""

        self._settings = settings

    async def search(
        self,
        query: str,
        trace_id: str,
    ) -> tuple[list[ResearchContext], ToolCallRecord]:
        """Execute a Tavily search and normalize results into citation contexts."""

        started_at = datetime.now(UTC)
        started_clock = monotonic()
        if not self._settings.tavily_api_key:
            return self._failure_record(
                query,
                trace_id,
                started_at,
                started_clock,
                "TAVILY_API_KEY is not configured.",
            )
        payload = {
            "query": query,
            "search_depth": self._settings.tavily_search_depth,
            "max_results": self._settings.tavily_max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.tavily_api_key}",
            "Content-Type": "application/json",
        }
        last_error: str | None = None
        for attempt in range(self._settings.tavily_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._settings.tavily_timeout_seconds,
                ) as client:
                    response = await client.post(
                        self._settings.tavily_base_url,
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
            except httpx.HTTPStatusError as error:
                last_error = str(error)
                if error.response.status_code in {429, 500, 502, 503, 504} and (
                    attempt < self._settings.tavily_max_retries
                ):
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                return self._failure_record(
                    query,
                    trace_id,
                    started_at,
                    started_clock,
                    last_error,
                )
            except httpx.HTTPError as error:
                last_error = str(error)
                if attempt < self._settings.tavily_max_retries:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                return self._failure_record(
                    query,
                    trace_id,
                    started_at,
                    started_clock,
                    last_error,
                )
            results = response.json().get("results", [])
            contexts = _normalize_tavily_results(results)
            finished_at = datetime.now(UTC)
            return contexts, ToolCallRecord(
                tool_name="tavily_web_search",
                status="completed",
                input_summary=query,
                output_summary=f"Returned {len(contexts)} web context(s).",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=(monotonic() - started_clock) * 1000,
                trace_id=trace_id,
            )
        return self._failure_record(
            query,
            trace_id,
            started_at,
            started_clock,
            last_error or "Unknown Tavily failure",
        )

    def _failure_record(
        self,
        query: str,
        trace_id: str,
        started_at: datetime,
        started_clock: float,
        error_message: str,
    ) -> tuple[list[ResearchContext], ToolCallRecord]:
        """Build a consistent failed audit record for Tavily errors."""

        finished_at = datetime.now(UTC)
        return [], ToolCallRecord(
            tool_name="tavily_web_search",
            status="failed",
            input_summary=query,
            output_summary="Web search failed.",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(monotonic() - started_clock) * 1000,
            error_message=error_message,
            trace_id=trace_id,
        )


def build_web_search_tool(settings: Settings) -> BaseWebSearchTool:
    """Build the Tavily-backed web search adapter."""

    return TavilyWebSearchTool(settings)


def _normalize_tavily_results(results: object) -> list[ResearchContext]:
    """Normalize Tavily result documents into citation-compatible contexts."""

    if not isinstance(results, list):
        return []
    normalized: list[ResearchContext] = []
    fetched_at = datetime.now(UTC)
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        content = str(result.get("content") or "").strip()
        if not content:
            continue
        source_uri = str(result.get("url") or f"web-result-{index}")
        normalized.append(
            ResearchContext(
                chunk_id=f"web:{index}:{source_uri}",
                document_id=f"web:{index}",
                title=str(result.get("title") or source_uri),
                source_uri=source_uri,
                text=content,
                score=float(result.get("score") or 0.0),
                source_backend="tavily",
                source_type="web",
                fetched_at=fetched_at,
                metadata={
                    "favicon": result.get("favicon"),
                },
            )
        )
    return normalized
