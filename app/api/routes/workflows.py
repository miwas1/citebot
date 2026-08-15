"""Evidence-backed business workflow endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from langgraph.errors import GraphInterrupt

from app.agents.schemas import ResearchQueryRequest
from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_admin_access, require_research_access
from app.workflows.registry import list_manifests
from app.workflows.renderers import evidence_bundle, render_markdown
from app.workflows.schemas import (
    ReviewDecisionRequest,
    ReviewResumeRequest,
    WorkflowRunRequest,
    WorkflowRunResponse,
)

router = APIRouter(prefix="/workflows")
project_router = APIRouter(prefix="/projects")
ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
ResearchAccessDependency = Annotated[None, Depends(require_research_access)]


@router.get("")
async def available_workflows(
    _: ResearchAccessDependency,
) -> list[dict[str, object]]:
    """List built-in workflow packs and their review policy."""

    return [manifest.model_dump(mode="json") for manifest in list_manifests()]


@router.post("/run", response_model=WorkflowRunResponse)
async def run_workflow(
    request: WorkflowRunRequest,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> WorkflowRunResponse:
    """Run grounded research and materialize a reviewable work product."""

    try:
        response = await container.research_agent_service.answer(
            ResearchQueryRequest(
                session_id=request.session_id,
                project_id="sample-project",
                query=request.query,
                top_k=request.top_k,
                allow_python_execution=request.allow_python_execution,
            ),
        )
        product = container.workflow_service.build_product(request.workflow_id, response)
        await container.workflow_repository.save(product)
        if product.status == "needs_review":
            try:
                await container.review_gate.start(product)
            except GraphInterrupt:
                # The product is already materialized; the interrupt is the durable review state.
                pass
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return WorkflowRunResponse(
        product=product,
        research=response.model_dump(mode="json"),
    )


@project_router.post("/{project_id}/workflows/run", response_model=WorkflowRunResponse)
async def run_project_workflow(
    project_id: str,
    request: WorkflowRunRequest,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> WorkflowRunResponse:
    """Run a reviewable workflow against one project's corpus."""

    await container.project_service.require(project_id, writable=True)
    try:
        response = await container.research_agent_service.answer(
            ResearchQueryRequest(
                session_id=request.session_id,
                project_id=project_id,
                query=request.query,
                top_k=request.top_k,
                allow_python_execution=request.allow_python_execution,
            )
        )
        product = container.workflow_service.build_product(request.workflow_id, response)
        await container.workflow_repository.save(product)
        if product.status == "needs_review":
            try:
                await container.review_gate.start(product)
            except GraphInterrupt:
                pass
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return WorkflowRunResponse(product=product, research=response.model_dump(mode="json"))


@router.get("/reviews/pending")
async def pending_reviews(
    container: ContainerDependency,
    _: Annotated[None, Depends(require_admin_access)],
) -> list[dict[str, object]]:
    """List products waiting for an authorized reviewer."""

    products = await container.workflow_repository.list_pending()
    return [product.model_dump(mode="json") for product in products]


@router.get("/reviews")
async def list_reviews(
    container: ContainerDependency,
    _: Annotated[None, Depends(require_admin_access)],
) -> list[dict[str, object]]:
    """List review-gated products through the plan's canonical review route."""

    products = await container.workflow_repository.list_pending()
    return [product.model_dump(mode="json") for product in products]


@router.get("/reviews/{work_product_id}")
async def get_review_product(
    work_product_id: str,
    container: ContainerDependency,
    _: Annotated[None, Depends(require_admin_access)],
) -> dict[str, object]:
    """Load one persisted work product and its review history."""

    product = await container.workflow_repository.get(work_product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Work product not found")
    return {
        "product": product.model_dump(mode="json"),
        "events": await container.workflow_repository.events(work_product_id),
        "checkpoint": await container.workflow_repository.checkpoint(work_product_id),
    }


@router.get("/{work_product_id}/export")
async def export_work_product(
    work_product_id: str,
    container: ContainerDependency,
    _: ResearchAccessDependency,
    format: str = "json",
) -> Response:
    """Export a product as JSON, Markdown, or a portable evidence bundle."""

    product = await container.workflow_repository.get(work_product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Work product not found")
    if format == "json":
        return JSONResponse(product.model_dump(mode="json"))
    if format == "markdown":
        return PlainTextResponse(render_markdown(product), media_type="text/markdown")
    if format == "bundle":
        payload = evidence_bundle(
            product,
            await container.workflow_repository.events(work_product_id),
        )
        return Response(
            payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{work_product_id}.zip"'
            },
        )
    raise HTTPException(status_code=400, detail="format must be json, markdown, or bundle")


@router.post("/reviews/{work_product_id}/decisions")
async def decide_review(
    work_product_id: str,
    request: ReviewDecisionRequest,
    container: ContainerDependency,
    _: Annotated[None, Depends(require_admin_access)],
) -> dict[str, object]:
    """Apply an approval, rejection, or reanalysis request atomically."""

    try:
        checkpoint = await container.workflow_repository.checkpoint(work_product_id)
        product = await container.workflow_repository.decide(
            work_product_id=work_product_id,
            actor_id=request.actor_id,
            action=request.action,
            comment=request.comment,
            expected_hash=request.expected_hash,
            edits=request.edits,
        )
        if checkpoint and checkpoint.get("status") == "needs_review":
            await container.review_gate.resume(
                product.analysis_run_id,
                request.action,
                request.comment,
            )
            if request.action == "edit" and product.status == "needs_review":
                await container.review_gate.restart(product)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"product": product.model_dump(mode="json")}


@router.post("/reviews/{work_product_id}/resume")
async def resume_review(
    work_product_id: str,
    request: ReviewResumeRequest,
    container: ContainerDependency,
    _: Annotated[None, Depends(require_admin_access)],
) -> dict[str, object]:
    """Resume a review-gated product after a durable decision or correction."""

    try:
        checkpoint = await container.workflow_repository.checkpoint(work_product_id)
        product = await container.workflow_repository.resume(
            work_product_id,
            request.actor_id,
            request.comment,
            request.expected_hash,
        )
        if checkpoint and checkpoint.get("status") == "needs_review":
            await container.review_gate.resume(
                product.analysis_run_id,
                "request_reanalysis",
                request.comment,
            )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"product": product.model_dump(mode="json")}
