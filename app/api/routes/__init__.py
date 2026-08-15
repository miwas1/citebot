"""Route registration helpers."""

from fastapi import APIRouter

from app.api.routes.admin_evaluation import router as admin_evaluation_router
from app.api.routes.admin_ingestion import project_router as project_admin_ingestion_router
from app.api.routes.admin_ingestion import router as admin_ingestion_router
from app.api.routes.analysis import router as analysis_router
from app.api.routes.conversations import project_router as project_conversations_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.documents import project_router as project_documents_router
from app.api.routes.documents import router as documents_router
from app.api.routes.health import router as health_router
from app.api.routes.projects import router as projects_router
from app.api.routes.research import project_router as project_research_router
from app.api.routes.research import router as research_router
from app.api.routes.workflows import project_router as project_workflows_router
from app.api.routes.workflows import router as workflows_router


def build_api_router() -> APIRouter:
    """Assemble the root API router."""

    router = APIRouter()
    router.include_router(health_router, tags=["health"])
    router.include_router(admin_evaluation_router, tags=["evaluation"])
    router.include_router(admin_ingestion_router, tags=["ingestion"])
    router.include_router(project_admin_ingestion_router, tags=["ingestion"])
    router.include_router(analysis_router, tags=["analysis"])
    router.include_router(documents_router, tags=["documents"])
    router.include_router(project_documents_router, tags=["documents"])
    router.include_router(conversations_router, tags=["conversations"])
    router.include_router(project_conversations_router, tags=["conversations"])
    router.include_router(research_router, tags=["research"])
    router.include_router(project_research_router, tags=["research"])
    router.include_router(projects_router, tags=["projects"])
    router.include_router(workflows_router, tags=["workflows"])
    router.include_router(project_workflows_router, tags=["workflows"])
    return router
