"""Deterministic calculation and structured comparison endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.calculation.schemas import CalculationCell, CalculationPlan
from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_research_access
from app.diffing.schemas import DocumentDiff, ElementSnapshot

router = APIRouter(prefix="/analysis")
ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
ResearchAccessDependency = Annotated[None, Depends(require_research_access)]


@router.post("/calculations")
async def run_calculation(
    plan: CalculationPlan,
    cells: list[CalculationCell],
    container: ContainerDependency,
    _: ResearchAccessDependency,
    analysis_run_id: str | None = None,
) -> dict[str, object]:
    """Execute a typed calculation with no arbitrary code execution."""

    try:
        result = container.calculation_service.execute(plan, cells)
        await container.calculation_repository.save(plan, result, analysis_run_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"result": result.model_dump(mode="json")}


@router.post("/diffs", response_model=DocumentDiff)
async def compare_versions(
    old_version_id: str,
    new_version_id: str,
    old_elements: list[ElementSnapshot],
    new_elements: list[ElementSnapshot],
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> DocumentDiff:
    """Compare structured elements and return exact source-linked changes."""

    diff = container.diff_service.compare(
        old_version_id,
        new_version_id,
        old_elements,
        new_elements,
    )
    await container.diff_repository.save(diff)
    return diff
