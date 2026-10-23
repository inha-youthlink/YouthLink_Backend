import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from openai import OpenAIError
from sqlalchemy.exc import SQLAlchemyError

from app.core.dependencies import PipelineDeps, get_chat_repository, get_pipeline_deps
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import (
    CreateRoomRequest,
    CreateRoomResponse,
    MessagesResponse,
    RoomsResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.services.chat_service import ChatService, RoomNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])

# 요청 body의 user_id와 같은 규칙 (chat_room.user_id VARCHAR(100))
UserIdQuery = Annotated[str, Query(min_length=1, max_length=100)]


# chat_service가 core.dependencies를 import해 순환 참조가 생기지 않도록 라우터 쪽에서 조립함
def get_chat_service(
    chat_repository: ChatRepository = Depends(get_chat_repository),
    pipeline_deps: PipelineDeps = Depends(get_pipeline_deps),
) -> ChatService:
    return ChatService(chat_repository, pipeline_deps)


# 외부 서비스 장애는 원인만 구분해 503으로 알리고, 상세 내용은 서버 로그에만 남김
@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except RoomNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "채팅방을 찾을 수 없습니다.")
    except OpenAIError:
        logger.exception("OpenAI 호출 실패")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM 서비스 호출에 실패했습니다.")
    except (SQLAlchemyError, OSError):
        logger.exception("DB 호출 실패")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "데이터베이스 호출에 실패했습니다.")


@router.post("", response_model=CreateRoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    request: CreateRoomRequest,
    service: ChatService = Depends(get_chat_service),
) -> CreateRoomResponse:
    with _http_errors():
        return await service.create_room(request)


@router.get("", response_model=RoomsResponse)
async def list_rooms(
    user_id: UserIdQuery,
    service: ChatService = Depends(get_chat_service),
) -> RoomsResponse:
    with _http_errors():
        return await service.list_rooms(user_id)


@router.post("/{room_id}/messages", response_model=SendMessageResponse)
async def send_message(
    room_id: UUID,
    request: SendMessageRequest,
    service: ChatService = Depends(get_chat_service),
) -> SendMessageResponse:
    with _http_errors():
        return await service.send_message(room_id, request)


@router.get("/{room_id}/messages", response_model=MessagesResponse)
async def list_messages(
    room_id: UUID,
    user_id: UserIdQuery,
    service: ChatService = Depends(get_chat_service),
) -> MessagesResponse:
    with _http_errors():
        return await service.list_messages(room_id, user_id)
