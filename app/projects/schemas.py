"""API schemas for reusable project workspaces."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectReadiness = Literal["empty", "preparing", "ready", "failed", "archived"]


class ProjectCreate(BaseModel):
    """Create a project that can receive documents."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class ProjectUpdate(BaseModel):
    """Update editable project metadata."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class ProjectSummary(BaseModel):
    """Project metadata and computed document readiness."""

    project_id: str
    name: str
    slug: str
    description: str | None = None
    status: str
    is_sample: bool = False
    document_count: int = 0
    ready_document_count: int = 0
    processing_document_count: int = 0
    failed_job_count: int = 0
    readiness: ProjectReadiness
    created_at: datetime
    updated_at: datetime

