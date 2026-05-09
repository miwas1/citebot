"""Research routes for LangGraph-backed question answering."""

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agents.schemas import (
    ResearchQueryRequest,
    ResearchResponse,
    create_session_id,
    create_trace_id,
)
from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_research_access

router = APIRouter(prefix="/research")

ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
ResearchAccessDependency = Annotated[None, Depends(require_research_access)]


@router.post("/query", response_model=ResearchResponse)
async def run_research_query(
    request: ResearchQueryRequest,
    http_request: Request,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> ResearchResponse:
    """Run the research agent against the indexed corpus and configured tools."""

    trace_id = getattr(http_request.state, "trace_id", None)
    return await container.research_agent_service.answer(request, trace_id=trace_id)


@router.post("/query/stream")
async def stream_research_query(
    request: ResearchQueryRequest,
    http_request: Request,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> StreamingResponse:
    """Stream research execution events and the final grounded response payload."""

    session_id = request.session_id or create_session_id()
    trace_id = getattr(http_request.state, "trace_id", None) or create_trace_id()
    request_with_session = request.model_copy(update={"session_id": session_id})

    async def event_stream() -> AsyncIterator[str]:
        """Yield a start event immediately and the final response when ready."""

        yield _stream_event(
            "start",
            {
                "session_id": session_id,
                "trace_id": trace_id,
            },
        )
        response = await container.research_agent_service.answer(
            request_with_session,
            trace_id=trace_id,
        )
        yield _stream_event(
            "complete",
            response.model_dump(mode="json"),
        )

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


def _stream_event(event_name: str, payload: dict[str, object]) -> str:
    """Encode one streaming event as a newline-delimited JSON line."""

    return json.dumps({"event": event_name, "data": payload}) + "\n"
