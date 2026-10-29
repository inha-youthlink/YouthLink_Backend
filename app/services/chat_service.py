import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any
from uuid import UUID

from openai import APITimeoutError, OpenAIError

from app.core.dependencies import PipelineDeps
from app.core.errors import (
    IdempotencyConflictError,
    SessionNotFoundError,
    StreamInProgressError,
)
from app.core.rate_limit import RateLimits
from app.core.session_lock import SessionLocks
from app.core.sse import sse_event
from app.pipeline import runner
from app.pipeline.conditions import RegionIndex
from app.repositories.chat_repository import ChatRepository, RoomRecord
from app.schemas.pipeline import (
    PUBLIC_TRACE_KEYS,
    PipelineInput,
    PipelineOutput,
    PolicyItem,
)
from app.schemas.session import (
    ChatRequest,
    ChatSession,
    Message,
    PolicySummary,
    SessionProfileRequest,
)
from app.services.profile_codes import to_profile, to_session_profile

logger = logging.getLogger(__name__)

# 청크 ID는 ETL이 청크를 다시 만들면 바뀔 수 있어 정책 번호·종류·인덱스로 청크를 남김
CHUNK_METADATA_KEYS = ("policy_no", "chunk_type", "chunk_index", "score")
# 채팅방 목록 한 줄에 들어갈 길이 (chat_room.title은 VARCHAR(200))
ROOM_TITLE_MAX_CHARS = 30
# 계약상 제목은 항상 문자열이라 첫 질문 전에는 이 문구로 보냄 (DB는 비워 둬 첫 질문으로 채움)
DEFAULT_SESSION_TITLE = "새 대화"

# 스트림 중 실패 (계약 §5.2 error 이벤트). 원인은 서버 로그에만 남김
STREAM_ERRORS = {
    "RAG_TIMEOUT": "답변을 만드는 데 시간이 너무 오래 걸렸어요. 다시 시도해 주세요.",
    "RAG_UPSTREAM_ERROR": "답변을 만드는 중 문제가 생겼어요. 다시 시도해 주세요.",
    "INTERNAL_ERROR": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
}


class ChatService:
    def __init__(
        self, chat_repository: ChatRepository, pipeline_deps: PipelineDeps, session_locks: SessionLocks
    ) -> None:
        self._chat_repository = chat_repository
        self._pipeline_deps = pipeline_deps
        self._session_locks = session_locks

    # 파이프라인과 /internal은 DB 코드 Profile을 그대로 쓰고, 계약 형식과의 변환은 여기서만 함
    async def create_session(self, client_id: str, request: SessionProfileRequest) -> ChatSession:
        regions = RegionIndex(await self._pipeline_deps.policy_repository.list_regions())
        room_id = await self._chat_repository.create_room(client_id, to_profile(request, regions))
        # 생성 시각은 DB가 정해 다시 읽음
        return _chat_session(await self._get_owned_room(room_id, client_id))

    async def list_sessions(self, client_id: str) -> list[ChatSession]:
        return [_chat_session(room) for room in await self._chat_repository.list_rooms(client_id)]

    async def get_session(self, room_id: UUID, client_id: str) -> ChatSession:
        return _chat_session(await self._get_owned_room(room_id, client_id))

    # 스트림을 시작하면 상태 코드를 바꿀 수 없어, 거절할 요청(세션 없음, 생성 중, 키 충돌, 하루 상한)은 여기서 JSON 오류로 끝냄
    # 돌려준 스트림은 SSE 이벤트 문자열을 내보내고, 끝나거나 끊기면 세션 잠금을 풂
    async def start_chat(
        self, room_id: UUID, client_id: str, request: ChatRequest, limits: RateLimits
    ) -> AsyncGenerator[str, None]:
        room = await self._get_owned_room(room_id, client_id)
        lock_key = str(room_id)
        if not self._session_locks.acquire(lock_key):
            raise StreamInProgressError
        try:
            question = await self._chat_repository.find_question(room_id, request.client_message_id)
            if question is not None and question.content != request.content:
                raise IdempotencyConflictError
            # done을 받지 못하고 다시 보낸 요청: 새로 만들지 않고 저장된 답변을 보냄
            if question is not None and question.answer_id is not None:
                return self._locked(lock_key, self._replay(question.answer_id, question.answer or ""))

            # 실제로 LLM을 부르는 요청만 셈 (뼈대·다시 보내기는 세지 않음)
            limits.use_llm()
            question_id = (
                question.message_id if question is not None
                # 답변 없이 남은 질문은 다시 저장하지 않고 답변만 만듦
                else await self._chat_repository.save_question(
                    room_id, request.client_message_id, request.content, _room_title(request.content)
                )
            )
            return self._locked(lock_key, self._generate(room, question_id, request.content))
        except BaseException:
            self._session_locks.release(lock_key)
            raise

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
                client_message_id=str(message.client_message_id) if message.client_message_id else None,
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

    # 끝까지 보내든, 실패하든, 연결이 끊겨 취소되든 잠금을 풂
    # 이벤트 사이에 닫히면(with_pings의 aclose) 안쪽 스트림도 바로 닫아 LLM 스트림이 열린 채 남지 않게 함
    async def _locked(self, lock_key: str, events: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        try:
            async with aclosing(events):
                async for event in events:
                    yield event
        finally:
            self._session_locks.release(lock_key)

    # 일반 답변과 같은 순서(카드 → 본문 → done)로 보냄. 본문은 저장된 전체를 token 하나로 (계약 §5.3, B9)
    async def _replay(self, answer_id: int, answer: str) -> AsyncGenerator[str, None]:
        refs = await self._chat_repository.list_policy_refs([answer_id])
        if policies := refs.get(answer_id):
            yield _policies_event(policies)
        yield sse_event("token", answer)
        yield sse_event("done", {"messageId": str(answer_id)})

    # 연결이 끊기면 이 스트림이 취소되거나 닫히고, aclosing이 파이프라인을 바로 닫아 LLM 스트림도 닫힘. 미완성 답변은 저장하지 않음
    async def _generate(self, room: RoomRecord, question_id: int, question: str) -> AsyncGenerator[str, None]:
        try:
            history = await self._chat_repository.list_recent_turns(
                room.room_id, self._pipeline_deps.settings.history_turns
            )
            current_policies = await self._chat_repository.list_current_policies(room.room_id)
            inp = PipelineInput(
                question=question, profile=room.profile, history=history, current_policies=current_policies
            )
            async with aclosing(runner.stream_pipeline(inp, self._pipeline_deps)) as events:
                async for event in events:
                    if isinstance(event, runner.PolicyCards):
                        # 카드가 없는 답변(인사, 조건 되묻기 등)은 policies를 보내지 않음 (B9)
                        if event.policies:
                            yield _policies_event(event.policies)
                    elif isinstance(event, runner.AnswerToken):
                        yield sse_event("token", event.text)
                    else:
                        answer_id = await self._save_answer(room, question_id, event)
                        yield sse_event("done", {"messageId": str(answer_id)})
        except APITimeoutError:
            logger.exception("답변 생성 시간 초과")
            yield _error_event("RAG_TIMEOUT")
        except OpenAIError:
            logger.exception("답변 생성 LLM 호출 실패")
            yield _error_event("RAG_UPSTREAM_ERROR")
        except Exception:
            logger.exception("답변 생성 실패")
            yield _error_event("INTERNAL_ERROR")

    async def _save_answer(self, room: RoomRecord, question_id: int, output: PipelineOutput) -> int:
        return await self._chat_repository.save_answer(
            room_id=room.room_id,
            question_id=question_id,
            answer=output.answer,
            answer_metadata=_answer_metadata(output),
            policies=output.policies,
            current_policies=output.current_policies,
        )


def _policies_event(policies: list[PolicyItem]) -> str:
    cards = [PolicySummary.from_item(item).model_dump(by_alias=True, mode="json") for item in policies]
    return sse_event("policies", cards)


def _error_event(code: str) -> str:
    return sse_event("error", {"code": code, "message": STREAM_ERRORS[code]})


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
        profile=to_session_profile(room.profile),
        created_at=room.created_at,
    )


def _room_title(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= ROOM_TITLE_MAX_CHARS else title[:ROOM_TITLE_MAX_CHARS].rstrip() + "…"
