"""Admin ingestion routes for corpus loading, search, and metrics."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.ingestion.schemas import (
    IngestionMetrics,
    IngestionRequest,
    JobStatusResponse,
    SearchRequest,
    SearchResult,
)

router = APIRouter(prefix="/admin/ingestion")

ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]


@router.post("/jobs", response_model=JobStatusResponse)
async def run_ingestion_job(
    request: IngestionRequest,
    container: ContainerDependency,
) -> JobStatusResponse:
    """Run a foreground ingestion job for the requested source path."""

    return await container.ingestion_service.ingest_path(
        source_path=Path(request.source_path),
        force_reindex=request.force_reindex,
        embedding_version=request.embedding_version,
        index_version=request.index_version,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_ingestion_job(
    job_id: str,
    container: ContainerDependency,
) -> JobStatusResponse:
    """Return the stored status for an ingestion job."""

    job = await container.ingestion_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/search", response_model=list[SearchResult])
async def search_ingested_chunks(
    request: SearchRequest,
    container: ContainerDependency,
) -> list[SearchResult]:
    """Search the locally persisted sparse index for validation workflows."""

    return await container.ingestion_service.search(request.query, request.top_k)


@router.get("/metrics", response_model=IngestionMetrics)
async def ingestion_metrics(
    container: ContainerDependency,
) -> IngestionMetrics:
    """Return aggregate ingestion counts for the current environment."""

    return await container.ingestion_service.metrics()
