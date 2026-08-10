"""Conversation history routes used by the end-user chat interface."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.agents.schemas import ConversationSummary, ResearchSessionRecord
from app.core.dependencies import get_container
from app.core.lifecycle import ServiceContainer
from app.core.security import require_research_access

router = APIRouter(prefix="/conversations")
ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
ResearchAccessDependency = Annotated[None, Depends(require_research_access)]


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> list[ConversationSummary]:
    """List persisted conversations for the chat sidebar."""

    return await container.research_session_store.list()


@router.get("/{session_id}", response_model=ResearchSessionRecord)
async def get_conversation(
    session_id: str,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> ResearchSessionRecord:
    """Return the stored turns and compressed memory for one conversation."""

    record = await container.research_session_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return record


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    session_id: str,
    container: ContainerDependency,
    _: ResearchAccessDependency,
) -> Response:
    """Delete a conversation from local persistence."""

    if not await container.research_session_store.delete(session_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
