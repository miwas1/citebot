"""Research routes for LangGraph-backed question answering."""

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agents.schemas import (
    ResearchQueryRequest,
    ResearchResponse,
    create_session_id,
    create_trace_id,
)
from app.core.admission import AdmissionRejected
from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_research_access

router = APIRouter(prefix="/research")
project_router = APIRouter(prefix="/projects")

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
    request = request.model_copy(update={"project_id": "sample-project"})
    try:
        async with container.research_admission.acquire():
            return await container.research_agent_service.answer(
                request,
                trace_id=trace_id,
            )
    except AdmissionRejected as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@project_router.post("/{project_id}/research/query", response_model=ResearchResponse)
async def run_project_research_query(
    project_id: str,
    request: ResearchQueryRequest,
    http_request: Request,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> ResearchResponse:
    """Run research against every current document in one project."""

    await container.project_service.require(project_id, writable=True)
    if request.session_id and await container.research_session_store.belongs_to_other_project(
        request.session_id, project_id
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    scoped_request = request.model_copy(update={"project_id": project_id})
    trace_id = getattr(http_request.state, "trace_id", None)
    try:
        async with container.research_admission.acquire():
            return await container.research_agent_service.answer(
                scoped_request, trace_id=trace_id
            )
    except AdmissionRejected as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


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
    request_with_session = request.model_copy(
        update={"session_id": session_id, "project_id": "sample-project"}
    )

    async def event_stream() -> AsyncIterator[str]:
        """Yield a start event immediately and the final response when ready."""

        try:
            async with container.research_admission.acquire():
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
        except AdmissionRejected as error:
            yield _stream_event("error", {"detail": str(error)})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@project_router.post("/{project_id}/research/query/stream")
async def stream_project_research_query(
    project_id: str,
    request: ResearchQueryRequest,
    http_request: Request,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> StreamingResponse:
    """Stream a project-scoped research execution."""

    await container.project_service.require(project_id, writable=True)
    if request.session_id and await container.research_session_store.belongs_to_other_project(
        request.session_id, project_id
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    scoped_request = request.model_copy(update={"project_id": project_id})
    session_id = scoped_request.session_id or create_session_id()
    trace_id = getattr(http_request.state, "trace_id", None) or create_trace_id()
    request_with_session = scoped_request.model_copy(update={"session_id": session_id})

    async def event_stream() -> AsyncIterator[str]:
        try:
            async with container.research_admission.acquire():
                yield _stream_event(
                    "start", {"session_id": session_id, "trace_id": trace_id}
                )
                response = await container.research_agent_service.answer(
                    request_with_session, trace_id=trace_id
                )
                yield _stream_event("complete", response.model_dump(mode="json"))
        except AdmissionRejected as error:
            yield _stream_event("error", {"detail": str(error)})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@project_router.get("/{project_id}/research/runs/{analysis_run_id}/evidence")
async def get_project_evidence_ledger(
    project_id: str,
    analysis_run_id: str,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> dict[str, object]:
    """Return evidence only for an analysis run in the selected project."""

    await container.project_service.require(project_id)
    ledger = await container.evidence_repository.get_run_evidence(
        analysis_run_id, project_id
    )
    if ledger is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return ledger


@router.get("/runs/{analysis_run_id}/evidence")
async def get_evidence_ledger(
    analysis_run_id: str,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> dict[str, object]:
    """Return the durable claim-to-anchor evidence ledger for one run."""

    ledger = await container.evidence_repository.get_run_evidence(
        analysis_run_id, "sample-project"
    )
    if ledger is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return ledger


def _stream_event(event_name: str, payload: dict[str, object]) -> str:
    """Encode one streaming event as a newline-delimited JSON line."""

    return json.dumps({"event": event_name, "data": payload}) + "\n"
