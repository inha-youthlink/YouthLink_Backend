from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.pipeline import PolicyItem, Profile

# chat_room.user_id VARCHAR(100)과 맞춰 DB 오류 대신 요청 검증에서 막음
UserId = Annotated[str, Field(min_length=1, max_length=100)]


class CreateRoomRequest(BaseModel):
    user_id: UserId
    profile: Profile = Field(default_factory=Profile)


class CreateRoomResponse(BaseModel):
    room_id: UUID


class SendMessageRequest(BaseModel):
    user_id: UserId
    question: str = Field(min_length=1)


# 정책 형식을 /internal/pipeline과 하나로 유지하려고 PolicyItem을 그대로 씀
class SendMessageResponse(BaseModel):
    message_id: int
    answer: str
    policies: list[PolicyItem]


# 응답 모양을 하나로 맞추려고 질문 메시지도 policies를 빈 목록으로 가짐
class MessageItem(BaseModel):
    message_id: int
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_at: datetime
    policies: list[PolicyItem] = Field(default_factory=list)


class MessagesResponse(BaseModel):
    messages: list[MessageItem]


class RoomItem(BaseModel):
    room_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class RoomsResponse(BaseModel):
    rooms: list[RoomItem]
