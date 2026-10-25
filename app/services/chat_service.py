from typing import Any
from uuid import UUID

from app.core.dependencies import PipelineDeps
from app.core.errors import SessionNotFoundError
from app.pipeline.runner import run_pipeline
from app.repositories.chat_repository import ChatRepository, RoomRecord
from app.schemas.chat import SendMessageRequest, SendMessageResponse
from app.schemas.pipeline import PUBLIC_TRACE_KEYS, PipelineInput, PipelineOutput, Profile
from app.schemas.session import ChatSession, Message, PolicySummary

# 청크 ID는 ETL이 청크를 다시 만들면 바뀔 수 있어 정책 번호·종류·인덱스로 청크를 남김
CHUNK_METADATA_KEYS = ("policy_no", "chunk_type", "chunk_index", "score")
# 채팅방 목록 한 줄에 들어갈 길이 (chat_room.title은 VARCHAR(200))
ROOM_TITLE_MAX_CHARS = 30
# 계약상 제목은 항상 문자열이라 첫 질문 전에는 이 문구로 보냄 (DB는 비워 둬 첫 질문으로 채움)
DEFAULT_SESSION_TITLE = "새 대화"


class ChatService:
    def __init__(self, chat_repository: ChatRepository, pipeline_deps: PipelineDeps) -> None:
        self._chat_repository = chat_repository
        self._pipeline_deps = pipeline_deps

    async def create_session(self, client_id: str, profile: Profile) -> ChatSession:
        room_id = await self._chat_repository.create_room(client_id, profile)
        # 생성 시각은 DB가 정해 다시 읽음
        return _chat_session(await self._get_owned_room(room_id, client_id))

    async def list_sessions(self, client_id: str) -> list[ChatSession]:
        return [_chat_session(room) for room in await self._chat_repository.list_rooms(client_id)]

    async def get_session(self, room_id: UUID, client_id: str) -> ChatSession:
        return _chat_session(await self._get_owned_room(room_id, client_id))

    # 저장은 답변까지 성공한 뒤에만 함. 파이프라인 예외는 저장 없이 호출 측으로 전달됨
    async def send_message(self, room_id: UUID, request: SendMessageRequest) -> SendMessageResponse:
        room = await self._get_owned_room(room_id, request.user_id)
        history = await self._chat_repository.list_recent_turns(
            room_id, self._pipeline_deps.settings.history_turns
        )
        current_policies = await self._chat_repository.list_current_policies(room_id)

        output = await run_pipeline(
            PipelineInput(
                question=request.question,
                profile=room.profile,
                history=history,
                current_policies=current_policies,
            ),
            self._pipeline_deps,
        )

        message_id = await self._chat_repository.save_turn(
            room_id=room_id,
            question=request.question,
            answer=output.answer,
            answer_metadata=_answer_metadata(output),
            policies=output.policies,
            current_policies=output.current_policies,
            room_title=_room_title(request.question),
        )
        return SendMessageResponse(message_id=message_id, answer=output.answer, policies=output.policies)

    async def list_messages(self, room_id: UUID, client_id: str) -> list[Message]:
        await self._get_owned_room(room_id, client_id)

        # 계약의 role은 user·assistant뿐이라 DB에만 있는 system·tool 메시지는 보내지 않음
        messages = [
            message
            for message in await self._chat_repository.list_messages(room_id)
            if message.role in ("user", "assistant")
        ]
        answer_ids = [message.message_id for message in messages if message.role == "assistant"]
        refs = await self._chat_repository.list_policy_refs(answer_ids)
        return [
            Message(
                id=str(message.message_id),
                role=message.role,
                content=message.content,
                # 계약상 질문 메시지는 null, 정책 없는 답변은 빈 목록
                policies=(
                    [PolicySummary.from_item(item) for item in refs.get(message.message_id, [])]
                    if message.role == "assistant"
                    else None
                ),
                created_at=message.created_at,
            )
            for message in messages
        ]

    async def _get_owned_room(self, room_id: UUID, client_id: str) -> RoomRecord:
        room = await self._chat_repository.get_room(room_id)
        if room is None or room.user_id != client_id:
            raise SessionNotFoundError
        return room


# 근거 원문은 크기가 커서 저장하지 않고, 어떤 청크가 검색됐는지만 남김
def _answer_metadata(output: PipelineOutput) -> dict[str, Any]:
    metadata = {key: value for key, value in output.trace.items() if key in PUBLIC_TRACE_KEYS}
    metadata["retrieved_chunks"] = [
        {key: chunk[key] for key in CHUNK_METADATA_KEYS}
        for chunk in output.trace.get("retrieved_chunks", [])
    ]
    return metadata


def _chat_session(room: RoomRecord) -> ChatSession:
    return ChatSession(
        id=room.room_id,
        title=room.title or DEFAULT_SESSION_TITLE,
        profile=room.profile,
        created_at=room.created_at,
    )


def _room_title(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= ROOM_TITLE_MAX_CHARS else title[:ROOM_TITLE_MAX_CHARS].rstrip() + "…"
