"""Admin evaluation routes for Phase 8 quality monitoring workflows."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_admin_access
from app.evaluation.schemas import EvaluationRunRequest, EvaluationRunResult

router = APIRouter(prefix="/admin/evaluation")

ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
AdminAccessDependency = Annotated[None, Depends(require_admin_access)]


@router.post("/runs", response_model=EvaluationRunResult)
async def run_evaluation(
    request: EvaluationRunRequest,
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> EvaluationRunResult:
    """Execute an evaluation run against the configured research pipeline."""

    return await container.evaluation_service.run(request)


@router.get("/runs/{run_id}", response_model=EvaluationRunResult)
async def get_evaluation_run(
    run_id: str,
    container: ContainerDependency,
    _: AdminAccessDependency,
) -> EvaluationRunResult:
    """Return a persisted evaluation run artifact by identifier."""

    run = await container.evaluation_service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return run
