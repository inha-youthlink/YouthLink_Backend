from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    ClientId,
    PipelineDeps,
    get_chat_repository,
    get_pipeline_deps,
    get_rate_limits,
    get_session_locks,
    limit_chat_requests,
    parse_session_id,
)
from app.core.rate_limit import RateLimits
from app.core.session_lock import SessionLocks
from app.core.sse import SSE_HEADERS, with_pings
from app.repositories.chat_repository import ChatRepository
from app.schemas.public import ErrorResponse
from app.schemas.session import (
    ChatRequest,
    ChatSession,
    CreateSessionRequest,
    Message,
    PolicySummary,
    SummaryResult,
)
from app.services.assist_service import AssistService
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
def get_assist_service(
    chat_repository: ChatRepository = Depends(get_chat_repository),
    pipeline_deps: PipelineDeps = Depends(get_pipeline_deps),
) -> AssistService:
    return AssistService(chat_repository, pipeline_deps)


def get_chat_service(
    chat_repository: ChatRepository = Depends(get_chat_repository),
    pipeline_deps: PipelineDeps = Depends(get_pipeline_deps),
    session_locks: SessionLocks = Depends(get_session_locks),
) -> ChatService:
    return ChatService(chat_repository, pipeline_deps, session_locks)


Service = Annotated[ChatService, Depends(get_chat_service)]
Assist = Annotated[AssistService, Depends(get_assist_service)]
SessionId = Annotated[UUID, Depends(parse_session_id)]


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


# 계약 R8: LLM을 부르므로 채팅과 같은 요청 제한과 하루 상한을 씀
@router.post(
    "/{session_id}/summary",
    responses={
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
        **_NOT_FOUND,
    },
    dependencies=[Depends(limit_chat_requests)],
)
async def create_summary(
    session_id: SessionId,
    client_id: ClientId,
    assist: Assist,
    limits: Annotated[RateLimits, Depends(get_rate_limits)],
) -> SummaryResult:
    return SummaryResult(summary=await assist.summarize(session_id, client_id, limits))


# 최대 10개, 신청 가능한 정책 우선 (계약 4.2, B3). LLM을 부르지 않아 채팅 요청 제한은 걸지 않음
@router.get("/{session_id}/recommendations", responses=_NOT_FOUND)
async def list_recommendations(session_id: SessionId, client_id: ClientId, assist: Assist) -> list[PolicySummary]:
    return await assist.recommend(session_id, client_id)


# 스트림을 시작하면 상태 코드를 바꿀 수 없어 세션·입력 오류는 시작 전에 JSON으로 응답함 (계약 §5.1)
@router.post(
    "/{session_id}/chat",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {"content": {"text/event-stream": {}}},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        **_NOT_FOUND,
    },
    dependencies=[Depends(limit_chat_requests)],
)
async def chat(
    session_id: SessionId,
    request: ChatRequest,
    client_id: ClientId,
    service: Service,
    settings: Annotated[Settings, Depends(get_settings)],
    limits: Annotated[RateLimits, Depends(get_rate_limits)],
) -> StreamingResponse:
    if settings.chat_skeleton_enabled:
        # 배포 경로 SSE 확인용. LLM을 부르지 않고 저장하지 않아 하루 상한에 세지 않음
        await service.get_session(session_id, client_id)
        events = skeleton_events(request.client_message_id, settings)
    else:
        events = await service.start_chat(session_id, client_id, request, limits)
    return StreamingResponse(
        with_pings(events, settings.sse_ping_interval), media_type="text/event-stream", headers=SSE_HEADERS
    )
