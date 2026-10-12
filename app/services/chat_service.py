from typing import Any
from uuid import UUID

from app.core.dependencies import PipelineDeps
from app.pipeline.runner import run_pipeline
from app.repositories.chat_repository import ChatRepository, RoomRecord
from app.schemas.chat import (
    CreateRoomRequest,
    CreateRoomResponse,
    MessagesResponse,
    RoomsResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.schemas.pipeline import PUBLIC_TRACE_KEYS, PipelineInput, PipelineOutput

# 청크 ID는 ETL이 청크를 다시 만들면 바뀔 수 있어 정책 번호·종류·인덱스로 청크를 남김
CHUNK_METADATA_KEYS = ("policy_no", "chunk_type", "chunk_index", "score")


class RoomNotFoundError(Exception):
    pass


class ChatService:
    def __init__(self, chat_repository: ChatRepository, pipeline_deps: PipelineDeps) -> None:
        self._chat_repository = chat_repository
        self._pipeline_deps = pipeline_deps

    async def create_room(self, request: CreateRoomRequest) -> CreateRoomResponse:
        room_id = await self._chat_repository.create_room(request.user_id, request.profile)
        return CreateRoomResponse(room_id=room_id)

    async def list_rooms(self, user_id: str) -> RoomsResponse:
        return RoomsResponse(rooms=await self._chat_repository.list_rooms(user_id))

    # 저장은 답변까지 성공한 뒤에만 함. 파이프라인 예외는 저장 없이 호출 측으로 전달됨
    async def send_message(self, room_id: UUID, request: SendMessageRequest) -> SendMessageResponse:
        room = await self._get_owned_room(room_id, request.user_id)

        # 대화 기록과 상태는 형태가 정해지는 7주차 질문 분석부터 넘김
        output = await run_pipeline(
            PipelineInput(question=request.question, profile=room.profile),
            self._pipeline_deps,
        )

        message_id = await self._chat_repository.save_turn(
            room_id=room_id,
            question=request.question,
            answer=output.answer,
            answer_metadata=_answer_metadata(output),
            policies=output.policies,
        )
        return SendMessageResponse(message_id=message_id, answer=output.answer, policies=output.policies)

    async def list_messages(self, room_id: UUID, user_id: str) -> MessagesResponse:
        await self._get_owned_room(room_id, user_id)

        messages = await self._chat_repository.list_messages(room_id)
        answer_ids = [message.message_id for message in messages if message.role == "assistant"]
        refs = await self._chat_repository.list_policy_refs(answer_ids)
        for message in messages:
            message.policies = refs.get(message.message_id, [])
        return MessagesResponse(messages=messages)

    # 다른 사용자의 채팅방이 있는지 알 수 없도록 없는 경우와 같은 오류로 처리함
    async def _get_owned_room(self, room_id: UUID, user_id: str) -> RoomRecord:
        room = await self._chat_repository.get_room(room_id)
        if room is None or room.user_id != user_id:
            raise RoomNotFoundError
        return room


# 근거 원문은 크기가 커서 저장하지 않고, 어떤 청크가 검색됐는지만 남김
def _answer_metadata(output: PipelineOutput) -> dict[str, Any]:
    metadata = {key: value for key, value in output.trace.items() if key in PUBLIC_TRACE_KEYS}
    metadata["retrieved_chunks"] = [
        {key: chunk[key] for key in CHUNK_METADATA_KEYS}
        for chunk in output.trace.get("retrieved_chunks", [])
    ]
    return metadata
