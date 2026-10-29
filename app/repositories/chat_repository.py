from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.policy_repository import PolicyRecord, fetch_region_labels
from app.schemas.chat import MessageItem
from app.schemas.pipeline import ConversationTurn, PolicyItem, PolicyRef, Profile
from app.schemas.policy_info import today_kst

_INSERT_ROOM_SQL = text("""
INSERT INTO chat_room (user_id)
VALUES (:user_id)
RETURNING room_id
""")

# 컬럼명이 Profile 필드명과 같아 model_dump() 결과를 그대로 바인딩함
_INSERT_PROFILE_SQL = text("""
INSERT INTO chat_profile (
    room_id, age, gender, region_code, marriage_status_code, annual_income,
    major_code, job_code, school_code, special_target_code
)
VALUES (
    :room_id, :age, :gender, :region_code, :marriage_status_code, :annual_income,
    :major_code, :job_code, :school_code, :special_target_code
)
""")

_ROOM_COLUMNS = """
SELECT r.room_id, r.user_id, r.title, r.created_at,
       p.age, p.gender, p.region_code, p.marriage_status_code, p.annual_income,
       p.major_code, p.job_code, p.school_code, p.special_target_code
FROM chat_room r
LEFT JOIN chat_profile p ON p.room_id = r.room_id
"""

_GET_ROOM_SQL = text(_ROOM_COLUMNS + """
WHERE r.room_id = :room_id
""")

# 계약(§4.1)이 세션 목록을 만든 순서로 정함
_LIST_ROOMS_SQL = text(_ROOM_COLUMNS + """
WHERE r.user_id = :user_id
ORDER BY r.created_at DESC
""")

# 같은 시각에 저장된 메시지도 있어 message_id로 순서를 보장함
_LIST_MESSAGES_SQL = text("""
SELECT message_id, role, content, client_message_id, created_at
FROM chat_message
WHERE room_id = :room_id
ORDER BY created_at, message_id
""")

_LIST_POLICY_REFS_SQL = text("""
SELECT r.message_id, r.relevance_score AS score,
       p.policy_no, p.policy_name,
       p.application_period_type_code, p.application_start_date, p.application_end_date,
       p.application_url, p.reference_url_1, p.reference_url_2, p.supervising_org_name, p.large_categories
FROM message_policy_ref r
JOIN policy p ON p.policy_no = r.policy_no
WHERE r.message_id = ANY(:message_ids)
ORDER BY r.message_id, r.display_order
""")

# 답변이 가리키는 질문으로 짝을 지어, 답변 없이 남은 질문(연결 끊김, 생성 실패)은 대화 기록에서 빠짐
# 최근 답변을 역순으로 자른 뒤 다시 시간순으로 돌려 오래된 턴부터 반환함
_LIST_RECENT_TURNS_SQL = text("""
SELECT question, answer_id, answer
FROM (
    SELECT q.content AS question, a.message_id AS answer_id, a.content AS answer
    FROM chat_message a
    JOIN chat_message q ON q.message_id = a.reply_to_message_id
    WHERE a.room_id = :room_id AND a.role = 'assistant'
    ORDER BY a.message_id DESC
    LIMIT :limit
) recent
ORDER BY answer_id
""")

# 재시도한 질문과 그 답변(있으면). 답변 다시 생성을 막지 않는 스키마라 가장 최근 답변을 씀
_FIND_QUESTION_SQL = text("""
SELECT q.message_id, q.content, a.message_id AS answer_id, a.content AS answer
FROM chat_message q
LEFT JOIN LATERAL (
    SELECT message_id, content
    FROM chat_message
    WHERE reply_to_message_id = q.message_id AND role = 'assistant'
    ORDER BY message_id DESC
    LIMIT 1
) a ON TRUE
WHERE q.room_id = :room_id AND q.client_message_id = :client_message_id AND q.role = 'user'
""")

_LIST_CURRENT_POLICIES_SQL = text("""
SELECT c.policy_no, p.policy_name
FROM chat_current_policy c
JOIN policy p ON p.policy_no = c.policy_no
WHERE c.room_id = :room_id
ORDER BY c.display_order
""")

_INSERT_MESSAGE_SQL = text("""
INSERT INTO chat_message (room_id, role, content, metadata, client_message_id, reply_to_message_id)
VALUES (:room_id, :role, :content, :metadata, :client_message_id, :reply_to_message_id)
RETURNING message_id
""").bindparams(bindparam("metadata", type_=JSONB))

_INSERT_POLICY_REF_SQL = text("""
INSERT INTO message_policy_ref (message_id, policy_no, relevance_score, display_order)
VALUES (:message_id, :policy_no, :relevance_score, :display_order)
""")

_DELETE_CURRENT_POLICIES_SQL = text("""
DELETE FROM chat_current_policy
WHERE room_id = :room_id
""")

_INSERT_CURRENT_POLICY_SQL = text("""
INSERT INTO chat_current_policy (room_id, policy_no, display_order)
VALUES (:room_id, :policy_no, :display_order)
""")

# 제목은 첫 질문으로만 정하고, 이후에는 바뀌지 않아 목록에서 같은 채팅방을 찾기 쉬움
_TOUCH_ROOM_SQL = text("""
UPDATE chat_room
SET updated_at = NOW(),
    title = COALESCE(title, :title)
WHERE room_id = :room_id
""")


@dataclass(frozen=True)
class RoomRecord:
    room_id: UUID
    user_id: str
    # 첫 질문 전에는 비어 있음 (save_question에서 채움)
    title: str | None
    created_at: datetime
    profile: Profile


@dataclass(frozen=True)
class QuestionRecord:
    message_id: int
    content: str
    # 답변까지 저장됐으면 그 답변
    answer_id: int | None
    answer: str | None


class ChatRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # 프로필 없는 채팅방이 남지 않도록 채팅방과 프로필을 한 트랜잭션으로 저장함
    async def create_room(self, user_id: str, profile: Profile) -> UUID:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(_INSERT_ROOM_SQL, {"user_id": user_id})
            room_id = result.scalar_one()
            await session.execute(_INSERT_PROFILE_SQL, {"room_id": room_id, **profile.model_dump()})
        return room_id

    async def get_room(self, room_id: UUID) -> RoomRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(_GET_ROOM_SQL, {"room_id": room_id})
            row = result.mappings().one_or_none()
        return None if row is None else _room_record(row)

    async def list_rooms(self, user_id: str) -> list[RoomRecord]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_ROOMS_SQL, {"user_id": user_id})
            return [_room_record(row) for row in result.mappings()]

    async def list_messages(self, room_id: UUID) -> list[MessageItem]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_MESSAGES_SQL, {"room_id": room_id})
            return [MessageItem.model_validate(row) for row in result.mappings()]

    async def list_policy_refs(self, message_ids: list[int]) -> dict[int, list[PolicyItem]]:
        if not message_ids:
            return {}

        # 지난 카드도 지금 신청 가능한지를 보여 주려고 조회 시점 기준으로 계산함
        today = today_kst()
        refs: dict[int, list[PolicyItem]] = {}
        async with self._session_factory() as session:
            rows = list((await session.execute(_LIST_POLICY_REFS_SQL, {"message_ids": message_ids})).mappings())
            labels = await fetch_region_labels(session, list({row["policy_no"] for row in rows}))
            for row in rows:
                record = PolicyRecord(**{column.name: row[column.name] for column in fields(PolicyRecord)})
                refs.setdefault(row["message_id"], []).append(
                    record.to_item(row["score"], today, labels.get(row["policy_no"]))
                )
        return refs

    async def list_recent_turns(self, room_id: UUID, turns: int) -> list[ConversationTurn]:
        if turns <= 0:
            return []

        async with self._session_factory() as session:
            result = await session.execute(_LIST_RECENT_TURNS_SQL, {"room_id": room_id, "limit": turns})
            rows = result.mappings().all()

        refs = await self.list_policy_refs([row["answer_id"] for row in rows])
        return [
            ConversationTurn(
                question=row["question"],
                answer=row["answer"],
                policies=[
                    PolicyRef(policy_no=policy.policy_no, policy_name=policy.policy_name)
                    for policy in refs.get(row["answer_id"], [])
                ],
            )
            for row in rows
        ]

    async def find_question(self, room_id: UUID, client_message_id: UUID) -> QuestionRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(
                _FIND_QUESTION_SQL, {"room_id": room_id, "client_message_id": client_message_id}
            )
            row = result.mappings().one_or_none()
        return None if row is None else QuestionRecord(**row)

    async def list_current_policies(self, room_id: UUID) -> list[PolicyRef]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_CURRENT_POLICIES_SQL, {"room_id": room_id})
            return [PolicyRef.model_validate(row) for row in result.mappings()]

    # 연결이 끊기거나 생성이 실패해도 질문은 남도록 답변 생성 전에 따로 저장함 (계약 Q3)
    async def save_question(self, room_id: UUID, client_message_id: UUID, question: str, room_title: str) -> int:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(_INSERT_MESSAGE_SQL, {
                "room_id": room_id, "role": "user", "content": question, "metadata": {},
                "client_message_id": client_message_id, "reply_to_message_id": None,
            })
            question_id = result.scalar_one()
            await session.execute(_TOUCH_ROOM_SQL, {"room_id": room_id, "title": room_title})
        return question_id

    # 정책 없는 답변이나 답변과 어긋난 현재 정책이 남지 않도록 답변 쪽은 한 트랜잭션으로 저장함
    async def save_answer(
        self,
        room_id: UUID,
        question_id: int,
        answer: str,
        answer_metadata: dict[str, Any],
        policies: list[PolicyItem],
        current_policies: list[PolicyRef],
    ) -> int:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(_INSERT_MESSAGE_SQL, {
                "room_id": room_id, "role": "assistant", "content": answer, "metadata": answer_metadata,
                "client_message_id": None, "reply_to_message_id": question_id,
            })
            answer_id = result.scalar_one()

            # "두 번째 거"를 카드 순서로 해석하도록 policies 순서를 1부터 저장함
            if policies:
                await session.execute(_INSERT_POLICY_REF_SQL, [
                    {
                        "message_id": answer_id,
                        "policy_no": policy.policy_no,
                        "relevance_score": policy.score,
                        "display_order": order,
                    }
                    for order, policy in enumerate(policies, start=1)
                ])

            # 현재 정책은 지금 상태만 두는 테이블이라 매 턴 지우고 다시 넣음 (이력은 답변 metadata)
            await session.execute(_DELETE_CURRENT_POLICIES_SQL, {"room_id": room_id})
            if current_policies:
                await session.execute(_INSERT_CURRENT_POLICY_SQL, [
                    {"room_id": room_id, "policy_no": policy.policy_no, "display_order": order}
                    for order, policy in enumerate(current_policies, start=1)
                ])
            # 제목은 질문을 저장할 때 정해져 있어 시각만 바꿈
            await session.execute(_TOUCH_ROOM_SQL, {"room_id": room_id, "title": None})
        return answer_id


def _room_record(row: Any) -> RoomRecord:
    return RoomRecord(
        room_id=row["room_id"],
        user_id=row["user_id"],
        title=row["title"],
        created_at=row["created_at"],
        profile=Profile(**{field: row[field] for field in Profile.model_fields}),
    )
