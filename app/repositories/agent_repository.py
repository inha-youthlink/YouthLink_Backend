from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.policy_repository import RECORD_COLUMNS, PolicyRecord, fetch_region_labels
from app.schemas.pipeline import PolicyItem
from app.schemas.policy_info import today_kst

# 추천 에이전트 대화 (chat_agent_*). 채팅 테이블과 따로 두고 이 저장소만 씀

_MESSAGE_COLUMNS = "message_id, role, content, question, options, created_at"

_LIST_MESSAGES_SQL = text(f"""
SELECT {_MESSAGE_COLUMNS}
FROM chat_agent_message
WHERE room_id = :room_id
ORDER BY message_id
""")

# 최근 것부터 자른 뒤 다시 오래된 순으로 돌려줌
_LIST_RECENT_MESSAGES_SQL = text(f"""
SELECT {_MESSAGE_COLUMNS}
FROM (
    SELECT {_MESSAGE_COLUMNS}
    FROM chat_agent_message
    WHERE room_id = :room_id
    ORDER BY message_id DESC
    LIMIT :limit
) recent
ORDER BY message_id
""")

# 카드를 바꾼 가장 최근 답변의 카드. 원천에서 사라진 정책은 신청할 수 없어 뺌 (계약 4.4)
_LATEST_CARDS_SQL = text(f"""
SELECT r.reason, {RECORD_COLUMNS}
FROM chat_agent_policy_ref r
JOIN policy p ON p.policy_no = r.policy_no
WHERE r.message_id = (
    SELECT m.message_id
    FROM chat_agent_message m
    WHERE m.room_id = :room_id AND m.cards_changed
    ORDER BY m.message_id DESC
    LIMIT 1
)
  AND p.is_active
ORDER BY r.display_order
""")

_INSERT_MESSAGE_SQL = text("""
INSERT INTO chat_agent_message (room_id, role, content, question, options, metadata, cards_changed)
VALUES (:room_id, :role, :content, :question, :options, :metadata, :cards_changed)
RETURNING message_id
""").bindparams(bindparam("options", type_=JSONB), bindparam("metadata", type_=JSONB))

_INSERT_CARD_SQL = text("""
INSERT INTO chat_agent_policy_ref (message_id, policy_no, display_order, reason)
VALUES (:message_id, :policy_no, :display_order, :reason)
""")

_DELETE_CONVERSATION_SQL = text("""
DELETE FROM chat_agent_message
WHERE room_id = :room_id
""")


@dataclass(frozen=True)
class AgentMessageRecord:
    message_id: int
    role: str
    content: str
    question: str | None
    options: list[str]
    created_at: datetime


@dataclass(frozen=True)
class AgentCardRecord:
    item: PolicyItem
    reason: str | None


# 저장할 에이전트 답변. metadata는 응답에 내보내지 않음 (도구 호출, trace)
@dataclass(frozen=True)
class AgentAnswer:
    content: str
    question: str | None = None
    options: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentCardRef:
    policy_no: str
    reason: str | None


class AgentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_messages(self, room_id: UUID) -> list[AgentMessageRecord]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_MESSAGES_SQL, {"room_id": room_id})
            return [AgentMessageRecord(**row) for row in result.mappings()]

    # 에이전트 입력용 (계약 4.8: 오래된 대화는 저장만 하고 최근 위주로 봄)
    async def list_recent_messages(self, room_id: UUID, limit: int) -> list[AgentMessageRecord]:
        if limit <= 0:
            return []
        async with self._session_factory() as session:
            result = await session.execute(_LIST_RECENT_MESSAGES_SQL, {"room_id": room_id, "limit": limit})
            return [AgentMessageRecord(**row) for row in result.mappings()]

    # 카드를 바꾼 답변이 없으면 빈 목록 (서비스가 시작 카드로 대신함)
    async def latest_cards(self, room_id: UUID) -> list[AgentCardRecord]:
        # 지난 카드도 지금 신청 가능한지를 보여 주려고 조회 시점 기준으로 계산함
        today = today_kst()
        async with self._session_factory() as session:
            rows = list((await session.execute(_LATEST_CARDS_SQL, {"room_id": room_id})).mappings())
            labels = await fetch_region_labels(session, [row["policy_no"] for row in rows])
        return [
            AgentCardRecord(
                item=PolicyRecord(**{column.name: row[column.name] for column in fields(PolicyRecord)}).to_item(
                    0.0, today, labels.get(row["policy_no"])
                ),
                reason=row["reason"],
            )
            for row in rows
        ]

    # 사용자 메시지·답변·카드를 한 번에 저장해 실패하면 아무것도 남지 않음 (계약 4.8, 그대로 다시 보내면 됨)
    # cards가 None이나 빈 목록이면 카드를 바꾸지 않은 답변 (계약 4.8: 빈 목록으로 지우는 일은 없음). 채팅방 목록 순서(updated_at)는 채팅 기준이라 건드리지 않음
    async def save_turn(
        self, room_id: UUID, user_content: str, answer: AgentAnswer, cards: list[AgentCardRef] | None
    ) -> int:
        async with self._session_factory() as session, session.begin():
            await session.execute(_INSERT_MESSAGE_SQL, {
                "room_id": room_id, "role": "user", "content": user_content, "question": None,
                "options": [], "metadata": {}, "cards_changed": False,
            })
            result = await session.execute(_INSERT_MESSAGE_SQL, {
                "room_id": room_id, "role": "assistant", "content": answer.content, "question": answer.question,
                "options": answer.options, "metadata": answer.metadata, "cards_changed": bool(cards),
            })
            answer_id = result.scalar_one()
            if cards:
                await session.execute(_INSERT_CARD_SQL, [
                    {"message_id": answer_id, "policy_no": card.policy_no, "display_order": order, "reason": card.reason}
                    for order, card in enumerate(cards, start=1)
                ])
        return answer_id

    # 카드 행은 FK CASCADE로 함께 지워짐. 채팅 메시지·현재 정책은 그대로
    async def delete_conversation(self, room_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(_DELETE_CONVERSATION_SQL, {"room_id": room_id})
