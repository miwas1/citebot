"""Research routes for LangGraph-backed question answering."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agents.schemas import ResearchQueryRequest, ResearchResponse
from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer

router = APIRouter(prefix="/research")

ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]


@router.post("/query", response_model=ResearchResponse)
async def run_research_query(
    request: ResearchQueryRequest,
    container: ContainerDependency,
) -> ResearchResponse:
    """Run the research agent against the indexed corpus and configured tools."""

    return await container.research_agent_service.answer(request)
