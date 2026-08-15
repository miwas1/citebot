"""Project workspace and scoped document routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_research_access
from app.projects.schemas import ProjectCreate, ProjectSummary, ProjectUpdate

router = APIRouter(prefix="/projects")
ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
ResearchAccessDependency = Annotated[None, Depends(require_research_access)]


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> list[ProjectSummary]:
    """List reusable projects and their current readiness."""

    return await container.project_repository.list()


@router.post("", response_model=ProjectSummary, status_code=status.HTTP_201_CREATED)
async def create_project(
    request: ProjectCreate,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> ProjectSummary:
    """Create an active project ready to receive uploads."""

    return await container.project_service.create(request)


@router.get("/{project_id}", response_model=ProjectSummary)
async def get_project(
    project_id: str,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> ProjectSummary:
    """Return one project and computed readiness counters."""

    return await container.project_service.require(project_id)


@router.patch("/{project_id}", response_model=ProjectSummary)
async def update_project(
    project_id: str,
    request: ProjectUpdate,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> ProjectSummary:
    """Update project metadata."""

    return await container.project_service.update(project_id, request)


@router.delete("/{project_id}", response_model=ProjectSummary)
async def archive_project(
    project_id: str,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> ProjectSummary:
    """Archive a project without deleting its evidence."""

    return await container.project_service.archive(project_id)
