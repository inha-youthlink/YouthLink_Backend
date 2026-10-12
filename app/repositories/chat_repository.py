from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.chat import MessageItem, RoomItem
from app.schemas.pipeline import PolicyItem, Profile

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

_GET_ROOM_SQL = text("""
SELECT r.room_id, r.user_id,
       p.age, p.gender, p.region_code, p.marriage_status_code, p.annual_income,
       p.major_code, p.job_code, p.school_code, p.special_target_code
FROM chat_room r
LEFT JOIN chat_profile p ON p.room_id = r.room_id
WHERE r.room_id = :room_id
""")

_LIST_ROOMS_SQL = text("""
SELECT room_id, title, created_at, updated_at
FROM chat_room
WHERE user_id = :user_id
ORDER BY updated_at DESC
""")

# 질문과 답변은 한 트랜잭션에 저장되어 created_at이 같으므로 message_id로 순서를 보장함
_LIST_MESSAGES_SQL = text("""
SELECT message_id, role, content, created_at
FROM chat_message
WHERE room_id = :room_id
ORDER BY created_at, message_id
""")

_LIST_POLICY_REFS_SQL = text("""
SELECT r.message_id, p.policy_no, p.policy_name,
       p.application_start_date, p.application_end_date, p.application_url,
       r.relevance_score AS score
FROM message_policy_ref r
JOIN policy p ON p.policy_no = r.policy_no
WHERE r.message_id = ANY(:message_ids)
ORDER BY r.message_id, r.display_order
""")

_INSERT_MESSAGE_SQL = text("""
INSERT INTO chat_message (room_id, role, content, metadata)
VALUES (:room_id, :role, :content, :metadata)
RETURNING message_id
""").bindparams(bindparam("metadata", type_=JSONB))

_INSERT_POLICY_REF_SQL = text("""
INSERT INTO message_policy_ref (message_id, policy_no, relevance_score, display_order)
VALUES (:message_id, :policy_no, :relevance_score, :display_order)
""")

_TOUCH_ROOM_SQL = text("""
UPDATE chat_room
SET updated_at = NOW()
WHERE room_id = :room_id
""")


@dataclass(frozen=True)
class RoomRecord:
    room_id: UUID
    user_id: str
    profile: Profile


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
        if row is None:
            return None
        profile = Profile(**{field: row[field] for field in Profile.model_fields})
        return RoomRecord(room_id=row["room_id"], user_id=row["user_id"], profile=profile)

    async def list_rooms(self, user_id: str) -> list[RoomItem]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_ROOMS_SQL, {"user_id": user_id})
            return [RoomItem.model_validate(row) for row in result.mappings()]

    async def list_messages(self, room_id: UUID) -> list[MessageItem]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_MESSAGES_SQL, {"room_id": room_id})
            return [MessageItem.model_validate(row) for row in result.mappings()]

    async def list_policy_refs(self, message_ids: list[int]) -> dict[int, list[PolicyItem]]:
        if not message_ids:
            return {}

        refs: dict[int, list[PolicyItem]] = {}
        async with self._session_factory() as session:
            result = await session.execute(_LIST_POLICY_REFS_SQL, {"message_ids": message_ids})
            for row in result.mappings():
                refs.setdefault(row["message_id"], []).append(PolicyItem.model_validate(row))
        return refs

    # 답변 없는 질문이나 정책 없는 답변이 남지 않도록 한 턴 전체를 한 트랜잭션으로 저장함
    async def save_turn(
        self,
        room_id: UUID,
        question: str,
        answer: str,
        answer_metadata: dict[str, Any],
        policies: list[PolicyItem],
    ) -> int:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                _INSERT_MESSAGE_SQL,
                {"room_id": room_id, "role": "user", "content": question, "metadata": {}},
            )
            result = await session.execute(
                _INSERT_MESSAGE_SQL,
                {"room_id": room_id, "role": "assistant", "content": answer, "metadata": answer_metadata},
            )
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
            await session.execute(_TOUCH_ROOM_SQL, {"room_id": room_id})
        return answer_id
