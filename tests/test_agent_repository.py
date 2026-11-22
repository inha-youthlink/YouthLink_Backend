import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.repositories.agent_repository import AgentAnswer, AgentCardRef
from app.schemas.pipeline import PolicyItem, PolicyRef, Profile
from tests.conftest import TEST_POLICY_NOS

USER_ID = "test-user"
P1, P2, P3 = TEST_POLICY_NOS


def _answer(content: str, question: str | None = None, options=()) -> AgentAnswer:
    return AgentAnswer(content=content, question=question, options=list(options), metadata={"tool_calls": 1})


def _cards(*policy_nos: str) -> list[AgentCardRef]:
    return [AgentCardRef(policy_no, f"{policy_no} 이유") for policy_no in policy_nos]


async def test_save_turn_and_list_messages(agent_repository, chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile())

    answer_id = await agent_repository.save_turn(
        room_id, "주거", _answer("월세 정책을 찾았어요.", "혼자 사세요?", ["네", "아니요"]), _cards(P1)
    )

    messages = await agent_repository.list_messages(room_id)
    assert [(m.role, m.content, m.question, m.options) for m in messages] == [
        ("user", "주거", None, []),
        ("assistant", "월세 정책을 찾았어요.", "혼자 사세요?", ["네", "아니요"]),
    ]
    assert messages[-1].message_id == answer_id


# 카드 저장이 실패하면 사용자 메시지·답변도 남지 않음 (계약 4.8: 그대로 다시 보내면 됨)
async def test_save_turn_is_atomic(agent_repository, chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile())

    with pytest.raises(IntegrityError):
        await agent_repository.save_turn(room_id, "주거", _answer("답변"), _cards(P1, "NOT-EXISTS"))

    assert await agent_repository.list_messages(room_id) == []


# 마지막 카드 = 카드를 바꾼 가장 최근 답변의 카드. 카드를 안 바꾼 답변이 뒤에 있어도 앞의 카드가 남음
async def test_latest_cards_from_last_changed_answer(agent_repository, chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    assert await agent_repository.latest_cards(room_id) == []

    await agent_repository.save_turn(room_id, "주거", _answer("답변1"), _cards(P2, P1))
    await agent_repository.save_turn(room_id, "고마워", _answer("답변2"), None)
    # 빈 목록도 카드를 바꾸지 않은 것으로 봄 (계약: 빈 목록으로 지우는 일은 없음)
    await agent_repository.save_turn(room_id, "음", _answer("답변3"), [])

    cards = await agent_repository.latest_cards(room_id)
    assert [(card.item.policy_no, card.item.policy_name, card.reason) for card in cards] == [
        (P2, "테스트 정책 02", f"{P2} 이유"),
        (P1, "테스트 정책 01", f"{P1} 이유"),
    ]

    await agent_repository.save_turn(room_id, "다른 거", _answer("답변4"), _cards(P3))
    assert [card.item.policy_no for card in await agent_repository.latest_cards(room_id)] == [P3]


async def test_latest_cards_skip_inactive_policy(agent_repository, chat_repository, session_factory):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    await agent_repository.save_turn(room_id, "주거", _answer("답변"), _cards(P1, P2))
    update = text("UPDATE policy SET is_active = :active, deactivated_at = :at WHERE policy_no = :no")
    async with session_factory() as session, session.begin():
        await session.execute(update, {"active": False, "at": datetime(2026, 11, 20, tzinfo=UTC), "no": P1})
    try:
        assert [card.item.policy_no for card in await agent_repository.latest_cards(room_id)] == [P2]
    finally:
        async with session_factory() as session, session.begin():
            await session.execute(update, {"active": True, "at": None, "no": P1})


async def test_recent_messages_keep_latest_in_order(agent_repository, chat_repository):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    for turn in range(1, 4):
        await agent_repository.save_turn(room_id, f"질문{turn}", _answer(f"답변{turn}"), None)

    recent = await agent_repository.list_recent_messages(room_id, 3)

    assert [m.content for m in recent] == ["답변2", "질문3", "답변3"]
    assert await agent_repository.list_recent_messages(room_id, 0) == []


# 새로 시작: 에이전트 대화만 지우고 채팅 대화·현재 정책·다른 방은 그대로
async def test_delete_conversation_only_agent_rows(agent_repository, chat_repository, session_factory):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    other_id = await chat_repository.create_room(USER_ID, Profile())
    question_id = await chat_repository.save_question(room_id, uuid.uuid4(), "채팅 질문", "제목")
    await chat_repository.save_answer(
        room_id, question_id, "채팅 답변", {}, [PolicyItem(policy_no=P1, policy_name="이름", score=0.5)],
        [PolicyRef(policy_no=P1, policy_name="이름")],
    )
    await agent_repository.save_turn(room_id, "주거", _answer("답변"), _cards(P1))
    await agent_repository.save_turn(other_id, "주거", _answer("답변"), _cards(P2))

    await agent_repository.delete_conversation(room_id)
    await agent_repository.delete_conversation(room_id)

    assert await agent_repository.list_messages(room_id) == []
    assert await agent_repository.latest_cards(room_id) == []
    async with session_factory() as session:
        cards = await session.execute(text("SELECT count(*) FROM chat_agent_policy_ref"))
        assert cards.scalar_one() == 1
    assert len(await chat_repository.list_messages(room_id)) == 2
    assert [p.policy_no for p in await chat_repository.list_current_policies(room_id)] == [P1]
    assert len(await agent_repository.list_messages(other_id)) == 2


# 채팅방을 지우면 에이전트 대화·카드도 함께 지워짐 (계약 4.1)
async def test_delete_room_cascades_agent_conversation(agent_repository, chat_repository, session_factory):
    room_id = await chat_repository.create_room(USER_ID, Profile())
    await agent_repository.save_turn(room_id, "주거", _answer("답변"), _cards(P1))

    assert await chat_repository.delete_room(room_id, USER_ID)

    async with session_factory() as session:
        counts = await session.execute(text(
            "SELECT (SELECT count(*) FROM chat_agent_message) + (SELECT count(*) FROM chat_agent_policy_ref)"
        ))
        assert counts.scalar_one() == 0
