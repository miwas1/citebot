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


@router.get("/jobs", response_model=list[JobStatusResponse])
async def list_document_jobs(
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[JobStatusResponse]:
    """List recent upload and ingestion jobs."""

    return await container.ingestion_service.list_jobs()


@router.get("/{document_id}/versions", response_model=list[DocumentVersionSummary])
async def list_document_versions(
    document_id: str,
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> list[DocumentVersionSummary]:
    """List immutable source versions for one logical document."""

    return await container.ingestion_service.list_versions(document_id)


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    container: ContainerDependency,
    _: AdminAccessDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> UploadResponse:
    """Stream one browser upload to local storage and submit it for ingestion."""

    safe_name = _safe_filename(filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {extension or 'none'}")

    upload_id = uuid4().hex
    upload_dir = container.settings.object_storage_path.parent / "uploads" / upload_id
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
        job = await container.ingestion_service.enqueue_path(destination)
    else:
        job = await container.ingestion_service.ingest_path(destination)
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
