from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.core.dependencies import (
    ClientId,
    PipelineDeps,
    get_agent_repository,
    get_chat_repository,
    get_pipeline_deps,
    get_session_locks,
    parse_session_id,
)
from app.core.session_lock import SessionLocks
from app.repositories.agent_repository import AgentRepository
from app.repositories.chat_repository import ChatRepository
from app.schemas.agent import AgentState
from app.schemas.public import ErrorResponse
from app.services.agent_service import AgentService

# 추천 에이전트 (계약 4.8). 경로는 세션 아래지만 sessions.py가 길어 파일을 나눔


# 응답이 X-Client-Id마다 달라 공용 캐시가 다른 사용자의 대화를 돌려주지 않게 막음
def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/sessions",
    tags=["agent"],
    dependencies=[Depends(_no_store)],
    responses={status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}},
)

_NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}}


# agent_service가 core.dependencies를 import해 순환 참조가 생기지 않게 라우터 쪽에서 조립함
def get_agent_service(
    chat_repository: ChatRepository = Depends(get_chat_repository),
    agent_repository: AgentRepository = Depends(get_agent_repository),
    pipeline_deps: PipelineDeps = Depends(get_pipeline_deps),
    session_locks: SessionLocks = Depends(get_session_locks),
) -> AgentService:
    return AgentService(chat_repository, agent_repository, pipeline_deps, session_locks)


Agent = Annotated[AgentService, Depends(get_agent_service)]
SessionId = Annotated[UUID, Depends(parse_session_id)]


# LLM을 쓰지 않아 채팅 요청 제한은 걸지 않음 (공개 API 공통 IP 제한만)
@router.get("/{session_id}/agent", responses=_NOT_FOUND)
async def get_agent(session_id: SessionId, client_id: ClientId, agent: Agent) -> AgentState:
    return await agent.get_state(session_id, client_id)


# 대화가 없어도 204. 에이전트 답변 중이면 409
@router.delete(
    "/{session_id}/agent",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}, **_NOT_FOUND},
)
async def reset_agent(session_id: SessionId, client_id: ClientId, agent: Agent) -> None:
    await agent.reset(session_id, client_id)
