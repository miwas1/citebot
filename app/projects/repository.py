"""Persistence helpers for project workspaces and readiness counts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import DocumentRecord, IngestionJobRecord, ProjectRecord
from app.db.session import DatabaseSessionManager
from app.projects.schemas import ProjectSummary


def slugify(value: str) -> str:
    """Convert a display name into a stable URL-safe slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:255] or "project"


class ProjectRepository:
    """Store project metadata and derive current readiness from source state."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        self._session_manager = session_manager

    async def ensure_system_project(
        self,
        project_id: str,
        name: str,
        slug: str,
        description: str,
        is_sample: bool = False,
    ) -> ProjectSummary:
        """Create or update a deterministic system project idempotently."""

        async with self._session_manager.session() as session:
            record = await session.get(ProjectRecord, project_id)
            if record is None:
                record = ProjectRecord(
                    project_id=project_id,
                    name=name,
                    slug=slug,
                    description=description,
                    is_sample=is_sample,
                    status="active",
                )
                session.add(record)
                await session.flush()
            else:
                record.name = name
                record.description = description
                record.is_sample = is_sample
            return await self._summary(session, record)

    async def create(self, name: str, description: str | None) -> ProjectSummary:
        """Create a user project with a collision-safe slug."""

        base_slug = slugify(name)
        async with self._session_manager.session() as session:
            slug = base_slug
            suffix = 2
            while await session.scalar(
                select(ProjectRecord.project_id).where(ProjectRecord.slug == slug)
            ):
                slug = f"{base_slug}-{suffix}"
                suffix += 1
            record = ProjectRecord(
                project_id=uuid4().hex,
                name=name.strip(),
                slug=slug,
                description=description,
                status="active",
            )
            session.add(record)
            await session.flush()
            return await self._summary(session, record)

    async def get(self, project_id: str) -> ProjectSummary | None:
        """Return one project with computed readiness."""

        async with self._session_manager.session() as session:
            record = await session.get(ProjectRecord, project_id)
            return await self._summary(session, record) if record else None

    async def list(self, limit: int = 100) -> list[ProjectSummary]:
        """Return active and archived projects, with sample first."""

        async with self._session_manager.session() as session:
            records = (
                await session.scalars(
                    select(ProjectRecord)
                    .order_by(ProjectRecord.is_sample.desc(), ProjectRecord.updated_at.desc())
                    .limit(limit)
                )
            ).all()
            return [await self._summary(session, record) for record in records]

    async def update(
        self,
        project_id: str,
        name: str | None,
        description: str | None,
    ) -> ProjectSummary | None:
        """Update metadata without allowing system identity to drift."""

        async with self._session_manager.session() as session:
            record = await session.get(ProjectRecord, project_id)
            if record is None:
                return None
            if name is not None and not record.is_sample:
                record.name = name.strip()
            if description is not None:
                record.description = description
            record.updated_at = datetime.now(tz=UTC)
            await session.flush()
            return await self._summary(session, record)

    async def archive(self, project_id: str) -> ProjectSummary | None:
        """Archive a project while retaining its documents and evidence."""

        async with self._session_manager.session() as session:
            record = await session.get(ProjectRecord, project_id)
            if record is None:
                return None
            if record.is_sample:
                return await self._summary(session, record)
            record.status = "archived"
            record.updated_at = datetime.now(tz=UTC)
            await session.flush()
            return await self._summary(session, record)

    async def _summary(self, session, record: ProjectRecord | None) -> ProjectSummary | None:
        """Build a project summary from document and job aggregates."""

        if record is None:
            return None
        document_count = int(
            await session.scalar(
                select(func.count(DocumentRecord.document_id)).where(
                    DocumentRecord.project_id == record.project_id
                )
            )
            or 0
        )
        ready_count = int(
            await session.scalar(
                select(func.count(DocumentRecord.document_id)).where(
                    DocumentRecord.project_id == record.project_id,
                )
            )
            or 0
        )
        # A persisted document is indexed by this application; active jobs cover
        # documents still being prepared and failed jobs explain a non-ready state.
        processing_count = int(
            await session.scalar(
                select(func.count(IngestionJobRecord.job_id)).where(
                    IngestionJobRecord.project_id == record.project_id,
                    IngestionJobRecord.status.in_(["queued", "running"]),
                )
            )
            or 0
        )
        failed_count = int(
            await session.scalar(
                select(func.count(IngestionJobRecord.job_id)).where(
                    IngestionJobRecord.project_id == record.project_id,
                    IngestionJobRecord.status.in_(["failed", "quarantined"]),
                )
            )
            or 0
        )
        if record.status == "archived":
            readiness = "archived"
        elif ready_count:
            readiness = "ready"
        elif processing_count:
            readiness = "preparing"
        elif failed_count:
            readiness = "failed"
        else:
            readiness = "empty"
        return ProjectSummary(
            project_id=record.project_id,
            name=record.name,
            slug=record.slug,
            description=record.description,
            status=record.status,
            is_sample=record.is_sample,
            document_count=document_count,
            ready_document_count=ready_count,
            processing_document_count=processing_count,
            failed_job_count=failed_count,
            readiness=readiness,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
