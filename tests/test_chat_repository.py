import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.repositories.chat_repository import ChatRepository
from app.schemas.pipeline import PolicyItem, Profile
from tests.conftest import TEST_POLICY_NOS

USER_ID = "test-user"


def _policy(policy_no: str, score: float) -> PolicyItem:
    return PolicyItem(policy_no=policy_no, policy_name="응답용 이름", score=score)


async def _save_turn(repo: ChatRepository, room_id, question: str, policies=(), metadata=None) -> int:
    return await repo.save_turn(
        room_id=room_id,
        question=question,
        answer=f"{question} 답변",
        answer_metadata=metadata or {},
        policies=list(policies),
    )


async def test_create_and_get_room(chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile(age=27, region_code="11680"))

    room = await chat_repository.get_room(room_id)

    assert room.user_id == USER_ID
    assert room.profile.age == 27
    assert room.profile.region_code == "11680"
    assert room.profile.annual_income is None


async def test_get_missing_room(chat_repository):
    assert await chat_repository.get_room(uuid.uuid4()) is None


# 같은 트랜잭션의 질문·답변은 created_at이 같아 message_id 순서가 깨지지 않는지 확인함
async def test_messages_keep_order(chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    await _save_turn(chat_repository, room_id, "질문1")
    await _save_turn(chat_repository, room_id, "질문2")

    messages = await chat_repository.list_messages(room_id)

    assert [(m.role, m.content) for m in messages] == [
        ("user", "질문1"),
        ("assistant", "질문1 답변"),
        ("user", "질문2"),
        ("assistant", "질문2 답변"),
    ]
    assert messages[0].created_at == messages[1].created_at


# 점수 순서가 아니라 저장한 카드 순서로 복원되는지 보려고 낮은 점수를 앞에 둠
async def test_policy_refs_keep_display_order(chat_repository, session_factory):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    answer_id = await _save_turn(chat_repository, room_id, "질문", policies=[
        _policy(TEST_POLICY_NOS[1], 0.3),
        _policy(TEST_POLICY_NOS[0], 0.9),
    ])

    refs = await chat_repository.list_policy_refs([answer_id])

    assert [(p.policy_no, p.score) for p in refs[answer_id]] == [
        (TEST_POLICY_NOS[1], 0.3),
        (TEST_POLICY_NOS[0], 0.9),
    ]
    # 이름은 응답 값이 아니라 policy 테이블의 현재 값으로 복원됨
    assert refs[answer_id][0].policy_name == "테스트 정책 02"
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT display_order FROM message_policy_ref WHERE message_id = :id ORDER BY display_order"),
            {"id": answer_id},
        )
        assert result.scalars().all() == [1, 2]


async def test_save_turn_without_policies(chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    answer_id = await _save_turn(chat_repository, room_id, "질문")

    assert await chat_repository.list_policy_refs([answer_id]) == {}
    assert len(await chat_repository.list_messages(room_id)) == 2


# dict가 JSON 문자열로 저장되면 metadata->'key' 조회가 깨지므로 객체 타입인지 확인함
async def test_metadata_saved_as_json_object(chat_repository, session_factory):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    metadata = {"chat_model": "m", "retrieved_chunks": [{"policy_no": TEST_POLICY_NOS[0], "chunk_index": 0}]}
    await _save_turn(chat_repository, room_id, "질문", metadata=metadata)

    async with session_factory() as session:
        result = await session.execute(
            text("""
                SELECT role, metadata, jsonb_typeof(metadata) AS kind
                FROM chat_message WHERE room_id = :room_id ORDER BY message_id
            """),
            {"room_id": room_id},
        )
        rows = result.mappings().all()

    assert [(r["role"], r["metadata"], r["kind"]) for r in rows] == [
        ("user", {}, "object"),
        ("assistant", metadata, "object"),
    ]


async def test_save_turn_updates_room_time(chat_repository, session_factory):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    await _save_turn(chat_repository, room_id, "질문")

    async with session_factory() as session:
        result = await session.execute(
            text("SELECT updated_at > created_at FROM chat_room WHERE room_id = :room_id"),
            {"room_id": room_id},
        )
        assert result.scalar_one() is True


async def test_list_rooms_by_user(chat_repository):
    older = await chat_repository.create_room(USER_ID, Profile())
    newer = await chat_repository.create_room(USER_ID, Profile())
    await chat_repository.create_room("other-user", Profile())
    await _save_turn(chat_repository, older, "질문")

    rooms = await chat_repository.list_rooms(USER_ID)

    # 늦게 만든 방보다 최근에 대화한 방이 위에 옴
    assert [room.room_id for room in rooms] == [older, newer]
    assert rooms[0].title is None


# 정책 저장이 실패하면 이미 넣은 질문·답변까지 취소되어야 함 (한 턴 한 트랜잭션)
async def test_save_turn_rolls_back_on_failure(chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    await _save_turn(chat_repository, room_id, "질문1")

    with pytest.raises(IntegrityError):
        await _save_turn(chat_repository, room_id, "질문2", policies=[_policy("NOT-EXISTS", 0.5)])

    messages = await chat_repository.list_messages(room_id)
    assert [m.content for m in messages] == ["질문1", "질문1 답변"]


# 회원 탈퇴 시 채팅방 삭제 한 번으로 하위 데이터가 모두 지워지는 전제를 확인함
async def test_delete_room_cascades(chat_repository, session_factory):
    room_id = await chat_repository.create_room(USER_ID, Profile(age=30))
    await _save_turn(chat_repository, room_id, "질문", policies=[_policy(TEST_POLICY_NOS[0], 0.5)])

    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM chat_room WHERE room_id = :room_id"), {"room_id": room_id})

    async with session_factory() as session:
        result = await session.execute(text("""
            SELECT (SELECT count(*) FROM chat_profile)
                 + (SELECT count(*) FROM chat_message)
                 + (SELECT count(*) FROM message_policy_ref)
        """))
        assert result.scalar_one() == 0
