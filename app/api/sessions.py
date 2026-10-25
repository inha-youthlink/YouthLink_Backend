from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import ClientId, PipelineDeps, get_chat_repository, get_pipeline_deps
from app.core.errors import SessionNotFoundError
from app.core.sse import SSE_HEADERS, with_pings
from app.repositories.chat_repository import ChatRepository
from app.schemas.public import ErrorResponse
from app.schemas.session import ChatRequest, ChatSession, CreateSessionRequest, Message
from app.services.chat_service import ChatService
from app.services.chat_skeleton import skeleton_events


# 응답이 X-Client-Id마다 달라 공용 캐시가 다른 사용자의 목록을 돌려주지 않게 막음
def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/sessions",
    tags=["sessions"],
    dependencies=[Depends(_no_store)],
    responses={status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}},
)

_NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}}


# chat_service가 core.dependencies를 import해 순환 참조가 생기지 않게 라우터 쪽에서 조립함
def get_chat_service(
    chat_repository: ChatRepository = Depends(get_chat_repository),
    pipeline_deps: PipelineDeps = Depends(get_pipeline_deps),
) -> ChatService:
    return ChatService(chat_repository, pipeline_deps)


# UUID가 아닌 id도 계약상 "없는 세션"이라 검증 오류(400) 대신 404로 응답함
def _session_id(session_id: str) -> UUID:
    try:
        return UUID(session_id)
    except ValueError:
        raise SessionNotFoundError from None


Service = Annotated[ChatService, Depends(get_chat_service)]
SessionId = Annotated[UUID, Depends(_session_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(request: CreateSessionRequest, client_id: ClientId, service: Service) -> ChatSession:
    return await service.create_session(client_id, request.profile)


@router.get("")
async def list_sessions(client_id: ClientId, service: Service) -> list[ChatSession]:
    return await service.list_sessions(client_id)


@router.get("/{session_id}", responses=_NOT_FOUND)
async def get_session(session_id: SessionId, client_id: ClientId, service: Service) -> ChatSession:
    return await service.get_session(session_id, client_id)


@router.get("/{session_id}/messages", responses=_NOT_FOUND)
async def list_messages(session_id: SessionId, client_id: ClientId, service: Service) -> list[Message]:
    return await service.list_messages(session_id, client_id)


# 스트림을 시작하면 상태 코드를 바꿀 수 없어 세션·입력 오류는 시작 전에 JSON으로 응답함 (계약 §5.1)
@router.post(
    "/{session_id}/chat",
    response_class=StreamingResponse,
    responses={status.HTTP_200_OK: {"content": {"text/event-stream": {}}}, **_NOT_FOUND},
)
async def chat(
    session_id: SessionId,
    request: ChatRequest,
    client_id: ClientId,
    service: Service,
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    await service.get_session(session_id, client_id)
    return StreamingResponse(
        with_pings(skeleton_events(request.client_message_id, settings), settings.sse_ping_interval),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
