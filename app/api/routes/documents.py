"""End-user document upload and library routes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_admin_access
from app.ingestion.schemas import (
    DocumentSummary,
    DocumentVersionSummary,
    JobStatusResponse,
    UploadResponse,
)

router = APIRouter(prefix="/documents")
project_router = APIRouter(prefix="/projects")
ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
AdminAccessDependency = Annotated[None, Depends(require_admin_access)]

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".md", ".json", ".jsonl", ".png", ".jpg", ".jpeg", ".tif", ".tiff"
}


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[DocumentSummary]:
    """List indexed documents in reverse chronological order."""

    return await container.ingestion_service.list_documents()


@project_router.get("/{project_id}/documents", response_model=list[DocumentSummary])
async def list_project_documents(
    project_id: str,
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[DocumentSummary]:
    """List only documents owned by one project."""

    await container.project_service.require(project_id)
    return await container.ingestion_service.list_documents(project_id=project_id)


@router.get("/jobs", response_model=list[JobStatusResponse])
async def list_document_jobs(
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[JobStatusResponse]:
    """List recent upload and ingestion jobs."""

    return await container.ingestion_service.list_jobs()


@project_router.get("/{project_id}/documents/jobs", response_model=list[JobStatusResponse])
async def list_project_document_jobs(
    project_id: str,
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[JobStatusResponse]:
    """List ingestion jobs belonging to one project."""

    await container.project_service.require(project_id)
    return await container.ingestion_service.list_jobs(project_id=project_id)


@router.get("/{document_id}/versions", response_model=list[DocumentVersionSummary])
async def list_document_versions(
    document_id: str,
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[DocumentVersionSummary]:
    """List immutable source versions for one logical document."""

    return await container.ingestion_service.list_versions(document_id)


@project_router.get(
    "/{project_id}/documents/{document_id}/versions",
    response_model=list[DocumentVersionSummary],
)
async def list_project_document_versions(
    project_id: str,
    document_id: str,
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[DocumentVersionSummary]:
    """List versions only when the document belongs to the project."""

    await container.project_service.require(project_id)
    documents = await container.ingestion_service.list_documents(project_id=project_id)
    if not any(document.document_id == document_id for document in documents):
        raise HTTPException(status_code=404, detail="Document not found")
    return await container.ingestion_service.list_versions(document_id)


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    container: ContainerDependency,
    _: AdminAccessDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> UploadResponse:
    """Stream an upload into the bundled Sample Project.

    Project-scoped uploads are the canonical API. This route remains as a
    compatibility shim for clients that have not yet added a project selector.
    """

    return await _upload_document(request, container, "sample-project", filename)


@project_router.post(
    "/{project_id}/documents/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_document(
    project_id: str,
    request: Request,
    container: ContainerDependency,
    _: AdminAccessDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> UploadResponse:
    """Stream one upload into the selected active project."""

    await container.project_service.require(project_id, writable=True)
    return await _upload_document(request, container, project_id, filename)


async def _upload_document(
    request: Request,
    container: ServiceContainer,
    project_id: str,
    filename: str,
) -> UploadResponse:
    """Stream and ingest one validated document for a project."""

    safe_name = _safe_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {extension or 'none'}")

    upload_id = uuid4().hex
    upload_dir = (
        container.settings.object_storage_path.parent
        / "uploads"
        / project_id
        / upload_id
    )
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / safe_name
    temporary = destination.with_suffix(destination.suffix + ".part")
    size = 0
    try:
        with temporary.open("xb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > container.settings.max_input_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds {container.settings.max_input_bytes} byte limit",
                    )
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    if container.settings.ingestion_execution_mode == "queued":
        job = await container.ingestion_service.enqueue_path(
            destination, project_id=project_id
        )
    else:
        job = await container.ingestion_service.ingest_path(
            destination, project_id=project_id
        )
    return UploadResponse(
        upload_id=upload_id,
        filename=safe_name,
        size_bytes=size,
        job=job,
    )


def _safe_filename(filename: str) -> str:
    """Remove path components and neutralize unsafe filename characters."""

    basename = Path(filename.replace("\\", "/")).name.strip()
    neutralized = re.sub(r"[^A-Za-z0-9._ -]", "_", basename).strip(" .")
    if not neutralized or neutralized in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return neutralized[:255]
