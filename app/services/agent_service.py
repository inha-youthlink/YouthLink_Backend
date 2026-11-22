import asyncio
from uuid import UUID

from app.core.dependencies import PipelineDeps
from app.core.errors import StreamInProgressError
from app.core.session_lock import SessionLocks
from app.pipeline.conditions import SearchConditions
from app.repositories.agent_repository import AgentMessageRecord, AgentRepository
from app.repositories.chat_repository import ChatRepository
from app.schemas.agent import START_MESSAGE_ID, AgentCard, AgentMessage, AgentState
from app.schemas.session import PolicySummary
from app.services.assist_service import profile_recommendations
from app.services.session_profile import get_owned_room, profile_conditions

# 추천 에이전트 (계약 4.8)

# 좁혀 주는 것이 에이전트의 역할이라 카드는 5개까지 (계약 4.8)
MAX_AGENT_CARDS = 5
# 첫 질문은 분야로 고정 (구상 문서 결정). 칩 글자 그대로 사용자 답으로 돌아옴
START_QUESTION = "어떤 도움이 필요하세요?"
START_OPTIONS = ["일자리", "주거", "교육", "복지·문화", "참여·권리"]


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
        cards = [
            AgentCard(policy=PolicySummary.from_item(card.item), reason=card.reason) for card in saved_cards
        ]
        # 카드를 바꾼 답변이 없거나 그 카드가 모두 비활성이 되면 패널이 비지 않게 시작 카드로 대신함
        if not cards:
            cards = await self._start_cards(conditions)
        return AgentState(messages=[start_message(conditions), *map(_message, messages)], cards=cards)

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
