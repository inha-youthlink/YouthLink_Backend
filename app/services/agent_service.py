import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import UTC, datetime
from uuid import UUID

from openai import APITimeoutError, OpenAIError

from app.core.dependencies import PipelineDeps
from app.core.errors import StreamInProgressError
from app.core.rate_limit import RateLimits
from app.core.session_lock import SessionLocks
from app.core.sse import sse_event
from app.llm.client import ToolCall
from app.pipeline.agent.recommend import (
    HISTORY_MESSAGES,
    START_OPTIONS,
    START_QUESTION,
    AgentTurnResult,
    run_agent_turn,
)
from app.pipeline.agent.tools import progress_text
from app.pipeline.conditions import SearchConditions
from app.repositories.agent_repository import AgentCardRecord, AgentMessageRecord, AgentRepository
from app.repositories.chat_repository import ChatRepository, RoomRecord
from app.schemas.agent import START_MESSAGE_ID, AgentCard, AgentMessage, AgentMessageRequest, AgentState
from app.schemas.session import PolicySummary
from app.services.assist_service import profile_recommendations
# 스트림 중 실패 코드·문구는 채팅과 같음 (계약 4.8 error). 원인은 서버 로그에만 남김
from app.services.chat_service import STREAM_ERRORS
from app.services.session_profile import get_owned_room, profile_conditions

# 추천 에이전트 (계약 4.8)

logger = logging.getLogger(__name__)


# 좁혀 주는 것이 에이전트의 역할이라 카드는 5개까지 (계약 4.8)
MAX_AGENT_CARDS = 5


# 채팅 잠금과 키를 나눠 채팅 답변 중에도 에이전트를 쓸 수 있게 함 (계약 4.8 동시 요청)
def agent_lock_key(room_id: UUID) -> str:
    return f"agent:{room_id}"


class AgentService:
    def __init__(
        self,
        chat_repository: ChatRepository,
        agent_repository: AgentRepository,
        pipeline_deps: PipelineDeps,
        session_locks: SessionLocks,
    ) -> None:
        self._chat_repository = chat_repository
        self._agent_repository = agent_repository
        self._deps = pipeline_deps
        self._session_locks = session_locks

    # LLM을 쓰지 않음. 시작 메시지는 저장하지 않고 항상 맨 앞에 붙임
    async def get_state(self, room_id: UUID, client_id: str) -> AgentState:
        room = await get_owned_room(self._chat_repository, room_id, client_id)
        conditions, messages, saved_cards = await asyncio.gather(
            profile_conditions(room, self._deps.policy_repository, self._deps.common_code_repository),
            self._agent_repository.list_messages(room_id),
            self._agent_repository.latest_cards(room_id),
        )
        cards = await self._shown_cards(conditions, saved_cards)
        return AgentState(messages=[start_message(conditions), *map(_message, messages)], cards=cards)

    # 스트림을 시작하면 상태 코드를 바꿀 수 없어, 거절할 요청(세션 없음, 답변 중, 하루 상한)은 여기서 JSON 오류로 끝냄
    # 돌려준 스트림은 SSE 이벤트 문자열을 내보내고, 끝나거나 끊기면 잠금을 풂
    async def start_turn(
        self, room_id: UUID, client_id: str, request: AgentMessageRequest, limits: RateLimits
    ) -> AsyncGenerator[str, None]:
        room = await get_owned_room(self._chat_repository, room_id, client_id)
        lock_key = agent_lock_key(room_id)
        if not self._session_locks.acquire(lock_key):
            raise StreamInProgressError
        try:
            limits.use_llm()
        except BaseException:
            self._session_locks.release(lock_key)
            raise
        return self._locked(lock_key, self._generate(room, request.content))

    # 끝까지 보내든, 실패하든, 연결이 끊겨 닫히든 잠금을 풂. 닫히면 안쪽(도구 루프)도 바로 닫음
    async def _locked(self, lock_key: str, events: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        try:
            async with aclosing(events):
                async for event in events:
                    yield event
        finally:
            self._session_locks.release(lock_key)

    # 저장한 뒤에 cards·message를 보냄 (계약 4.8). 그 전에 실패하거나 끊기면 아무것도 저장하지 않음
    async def _generate(self, room: RoomRecord, content: str) -> AsyncGenerator[str, None]:
        try:
            conditions, history, saved_cards = await asyncio.gather(
                profile_conditions(room, self._deps.policy_repository, self._deps.common_code_repository),
                self._agent_repository.list_recent_messages(room.room_id, HISTORY_MESSAGES),
                self._agent_repository.latest_cards(room.room_id),
            )
            shown = [card.policy for card in await self._shown_cards(conditions, saved_cards)]
            turn = run_agent_turn(self._deps, conditions, history, shown, content)
            result: AgentTurnResult | None = None
            deadline = self._deps.settings.agent_timeout
            async with aclosing(turn) as events, aclosing(_with_deadline(events, deadline)) as timed:
                async for event in timed:
                    if isinstance(event, ToolCall):
                        yield sse_event("progress", {"text": progress_text(event)})
                    else:
                        result = event
            if result is None:
                raise RuntimeError("추천 에이전트 턴이 결과 없이 끝남")

            answer = result.answer
            message_id = await self._agent_repository.save_turn(room.room_id, content, answer, result.cards)
            if result.cards:
                # GET과 같은 카드가 가도록 저장한 카드를 다시 읽음 (비활성 정책 제외, 지역 표시)
                # 저장 직후 비활성이 되어 모두 빠지면 계약(1~5개)에 맞게 보내지 않음
                if cards := await self._agent_repository.latest_cards(room.room_id):
                    yield sse_event("cards", [_card(card).model_dump(by_alias=True, mode="json") for card in cards])
            message = AgentMessage(
                id=str(message_id), role="assistant", content=answer.content, question=answer.question,
                options=answer.options if answer.question else [],
                # 저장 시각과 몇 밀리초 차이. GET은 DB 시각을 보냄
                created_at=datetime.now(UTC),
            )
            yield sse_event("message", message.model_dump(by_alias=True, mode="json"))
            yield sse_event("done", {"messageId": str(message_id)})
        except (APITimeoutError, TimeoutError):
            logger.exception("추천 에이전트 시간 초과")
            yield _error_event("RAG_TIMEOUT")
        except OpenAIError:
            logger.exception("추천 에이전트 LLM 호출 실패")
            yield _error_event("RAG_UPSTREAM_ERROR")
        except Exception:
            logger.exception("추천 에이전트 답변 실패")
            yield _error_event("INTERNAL_ERROR")

    # 카드를 바꾼 답변이 없거나 그 카드가 모두 비활성이 되면 패널이 비지 않게 시작 카드로 대신함
    async def _shown_cards(self, conditions: SearchConditions, saved: list[AgentCardRecord]) -> list[AgentCard]:
        return [_card(card) for card in saved] or await self._start_cards(conditions)

    # 답변을 만드는 중에 지우면 그 답변이 지운 뒤에 저장될 수 있어 잠금을 잡은 채 지움
    async def reset(self, room_id: UUID, client_id: str) -> None:
        await get_owned_room(self._chat_repository, room_id, client_id)
        lock_key = agent_lock_key(room_id)
        if not self._session_locks.acquire(lock_key):
            raise StreamInProgressError
        try:
            await self._agent_repository.delete_conversation(room_id)
        finally:
            self._session_locks.release(lock_key)

    # 추천 정책(4.2)과 같은 기준이되 분야로 좁히지 않음 (첫 질문이 분야를 묻기 때문)
    async def _start_cards(self, conditions: SearchConditions) -> list[AgentCard]:
        policies = await profile_recommendations(self._deps.policy_repository, conditions, None, MAX_AGENT_CARDS)
        return [AgentCard(policy=policy, reason=None) for policy in policies]


# 사용자가 어떤 프로필로 찾는지 알 수 있게 지역 · 나이 · 취업 상태를 보여 줌 (목업)
def start_message(conditions: SearchConditions) -> AgentMessage:
    parts = [
        conditions.region.label if conditions.region else None,
        f"만 {conditions.age.code}세" if conditions.age else None,
        conditions.job.label if conditions.job else None,
    ]
    profile = " · ".join(part for part in parts if part)
    target = f"이 대화의 프로필({profile})에" if profile else "이 대화의 프로필에"
    return AgentMessage(
        id=START_MESSAGE_ID,
        role="assistant",
        content=f"안녕하세요! {target} 맞는 정책을 찾아 드릴게요.\n몇 가지만 여쭤볼게요.",
        question=START_QUESTION,
        options=START_OPTIONS,
        created_at=None,
    )


def _message(record: AgentMessageRecord) -> AgentMessage:
    return AgentMessage(
        id=str(record.message_id),
        role=record.role,
        content=record.content,
        question=record.question,
        # 계약: question이 없으면 항상 빈 목록
        options=record.options if record.question else [],
        created_at=record.created_at,
    )


def _card(card: AgentCardRecord) -> AgentCard:
    return AgentCard(policy=PolicySummary.from_item(card.item), reason=card.reason)


def _error_event(code: str) -> str:
    return sse_event("error", {"code": code, "message": STREAM_ERRORS[code]})


# 도구 루프 전체의 시간 제한. with_pings가 이벤트마다 다른 태스크에서 꺼내 asyncio.timeout을 못 써 남은 시간으로 기다림
async def _with_deadline[T](events: AsyncGenerator[T, None], seconds: float) -> AsyncGenerator[T, None]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        try:
            event = await asyncio.wait_for(anext(events), max(deadline - loop.time(), 0))
        except StopAsyncIteration:
            return
        yield event
