"""Project lifecycle and scope validation service."""

from __future__ import annotations

from fastapi import HTTPException

from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectSummary, ProjectUpdate

SAMPLE_PROJECT_ID = "sample-project"


class ProjectService:
    """Expose project operations and fail-closed active-project checks."""

    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    async def ensure_sample_project(self) -> None:
        """Create the bundled sample workspace when the app starts."""

        await self.repository.ensure_system_project(
            SAMPLE_PROJECT_ID,
            "Sample Project",
            "sample",
            "100 recent machine-learning papers from arXiv's cs.LG category.",
            is_sample=True,
        )

    async def require(self, project_id: str, *, writable: bool = False) -> ProjectSummary:
        project = await self.repository.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if writable and project.status == "archived":
            raise HTTPException(status_code=409, detail="Project is archived")
        return project

    async def create(self, request: ProjectCreate) -> ProjectSummary:
        return await self.repository.create(request.name, request.description)

    async def update(self, project_id: str, request: ProjectUpdate) -> ProjectSummary:
        await self.require(project_id, writable=True)
        project = await self.repository.update(
            project_id, request.name, request.description
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    async def archive(self, project_id: str) -> ProjectSummary:
        project = await self.require(project_id)
        if project.is_sample:
            raise HTTPException(status_code=409, detail="The Sample Project cannot be archived")
        archived = await self.repository.archive(project.project_id)
        if archived is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return archived
