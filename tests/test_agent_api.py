import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.agent import get_agent_service
from app.core.config import get_settings
from app.core.session_lock import SessionLocks
from app.main import app
from app.repositories.agent_repository import AgentCardRecord, AgentMessageRecord
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import PolicyItem, Profile
from app.services.agent_service import AgentService, agent_lock_key
from tests.conftest import (
    CLIENT_ID,
    HEADERS,
    OTHER_CLIENT_ID,
    FakeChatRepository,
    FakeCodes,
    FakePolicyRepository,
)

PROFILE = Profile(age=27, region_code="11680", job_code="0013003", special_target_code="0014010")
SAVED_AT = datetime(2026, 11, 22, 3, 0, tzinfo=UTC)


def _record(policy_no: str) -> PolicyRecord:
    return PolicyRecord(
        policy_no=policy_no, policy_name=f"정책 {policy_no}", application_period_type_code="0057002",
        application_start_date=None, application_end_date=None, application_url=None,
        reference_url_1=None, reference_url_2=None, supervising_org_name="서울특별시", large_categories=["주거"],
    )


class SearchPolicyRepository(FakePolicyRepository):
    def __init__(self) -> None:
        super().__init__()
        self.calls = []

    async def search(self, policy_filter, today, offset, limit, *, available_first=False):
        self.calls.append((policy_filter, offset, limit, available_first))
        return [_record(f"P-{n}") for n in range(1, limit + 1)], 20

    async def get_region_labels(self, policy_nos):
        return {no: "서울특별시" for no in policy_nos}


class FakeAgentRepository:
    def __init__(self) -> None:
        self.messages: dict[uuid.UUID, list[AgentMessageRecord]] = {}
        self.cards: dict[uuid.UUID, list[AgentCardRecord]] = {}

    async def list_messages(self, room_id):
        return self.messages.get(room_id, [])

    async def latest_cards(self, room_id):
        return self.cards.get(room_id, [])

    async def delete_conversation(self, room_id):
        self.messages.pop(room_id, None)
        self.cards.pop(room_id, None)


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def agents() -> FakeAgentRepository:
    return FakeAgentRepository()


@pytest.fixture
def policies() -> SearchPolicyRepository:
    return SearchPolicyRepository()


@pytest.fixture
def locks() -> SessionLocks:
    return SessionLocks()


@pytest.fixture
def client(repo, agents, policies, locks):
    deps = SimpleNamespace(
        policy_repository=policies, common_code_repository=FakeCodes({"0013003": "미취업자"}), settings=get_settings()
    )
    app.dependency_overrides[get_agent_service] = lambda: AgentService(repo, agents, deps, locks)
    yield TestClient(app)
    app.dependency_overrides.pop(get_agent_service, None)


@pytest.fixture
def room_id(repo) -> uuid.UUID:
    return asyncio.run(repo.create_room(CLIENT_ID, PROFILE))


def _get(client, room_id, headers=HEADERS):
    return client.get(f"/api/sessions/{room_id}/agent", headers=headers)


def _delete(client, room_id, headers=HEADERS):
    return client.delete(f"/api/sessions/{room_id}/agent", headers=headers)


# 처음 열면 저장하지 않는 시작 메시지 1개 + 프로필 기준 시작 카드 5개 (계약 4.8)
def test_start_state(client, policies, room_id):
    response = _get(client, room_id)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["messages"] == [{
        "id": "start",
        "role": "assistant",
        "content": "안녕하세요! 이 대화의 프로필(서울특별시 강남구 · 만 27세 · 미취업자)에 맞는 정책을 찾아 드릴게요.\n"
                   "몇 가지만 여쭤볼게요.",
        "question": "어떤 도움이 필요하세요?",
        "options": ["일자리", "주거", "교육", "복지·문화", "참여·권리"],
        "createdAt": None,
    }]
    assert [card["policy"]["id"] for card in body["cards"]] == ["P-1", "P-2", "P-3", "P-4", "P-5"]
    assert all(card["reason"] is None for card in body["cards"])
    # 추천 정책(4.2)과 같은 필터·순서이되 분야로 좁히지 않음
    policy_filter, offset, limit, available_first = policies.calls[0]
    assert (offset, limit, available_first) == (0, 5, True)
    assert policy_filter.exclude_closed is True
    assert policy_filter.large_categories == ()
    assert policy_filter.region_codes


def test_saved_conversation_follows_start_message(client, agents, policies, room_id):
    agents.messages[room_id] = [
        AgentMessageRecord(1, "user", "주거", None, [], SAVED_AT),
        AgentMessageRecord(2, "assistant", "월세 정책을 골랐어요.", "혼자 사세요?", ["네", "아니요"], SAVED_AT),
        # 질문 없는 답변은 options가 남아 있어도 빈 목록으로 보냄 (계약 3.5)
        AgentMessageRecord(3, "assistant", "도움이 됐길 바라요.", None, ["남은 값"], SAVED_AT),
    ]
    agents.cards[room_id] = [
        AgentCardRecord(PolicyItem(policy_no="P-RENT", policy_name="청년 월세 지원", score=0.0), "혼자 살아서"),
    ]

    body = _get(client, room_id).json()

    assert [(m["id"], m["role"], m["question"], m["options"]) for m in body["messages"]] == [
        ("start", "assistant", "어떤 도움이 필요하세요?", ["일자리", "주거", "교육", "복지·문화", "참여·권리"]),
        ("1", "user", None, []),
        ("2", "assistant", "혼자 사세요?", ["네", "아니요"]),
        ("3", "assistant", None, []),
    ]
    assert body["messages"][1]["createdAt"] == "2026-11-22T12:00:00+09:00"
    assert [(card["policy"]["id"], card["reason"]) for card in body["cards"]] == [("P-RENT", "혼자 살아서")]
    # 저장된 카드가 있으면 시작 카드를 찾지 않음
    assert policies.calls == []


# 대화는 있지만 카드를 바꾼 답변이 없거나 그 카드가 모두 비활성이 된 경우: 패널이 비지 않게 시작 카드
def test_no_saved_cards_falls_back_to_start_cards(client, agents, room_id):
    agents.messages[room_id] = [AgentMessageRecord(1, "user", "안녕", None, [], SAVED_AT)]

    body = _get(client, room_id).json()

    assert len(body["cards"]) == 5
    assert all(card["reason"] is None for card in body["cards"])


@pytest.mark.parametrize("method", [_get, _delete])
def test_other_users_or_missing_session(client, repo, method):
    other_room = asyncio.run(repo.create_room(OTHER_CLIENT_ID, PROFILE))

    for room in (other_room, uuid.uuid4(), "not-a-uuid"):
        response = method(client, room)
        assert response.status_code == 404
        assert response.json()["code"] == "SESSION_NOT_FOUND"


def test_requires_client_id(client, room_id):
    assert _get(client, room_id, headers={}).json()["code"] == "INVALID_CLIENT_ID"


# 새로 시작: 지운 뒤 다시 열면 시작 상태. 대화가 없어도 204
def test_reset_then_start_state(client, agents, room_id):
    agents.messages[room_id] = [AgentMessageRecord(1, "user", "주거", None, [], SAVED_AT)]

    first = _delete(client, room_id)
    second = _delete(client, room_id)

    assert (first.status_code, second.status_code) == (204, 204)
    assert [m["id"] for m in _get(client, room_id).json()["messages"]] == ["start"]


# 에이전트 답변 중이면 지우지 않음. 채팅 잠금과는 키가 달라 채팅 답변 중에는 지울 수 있음
def test_reset_while_agent_answering(client, agents, locks, room_id):
    agents.messages[room_id] = [AgentMessageRecord(1, "user", "주거", None, [], SAVED_AT)]
    assert locks.acquire(agent_lock_key(room_id))

    response = _delete(client, room_id)

    assert response.status_code == 409
    assert response.json()["code"] == "STREAM_IN_PROGRESS"
    assert agents.messages[room_id]
    locks.release(agent_lock_key(room_id))

    assert locks.acquire(str(room_id))
    assert _delete(client, room_id).status_code == 204
    # 지운 뒤 잠금이 풀려 다음 답변을 시작할 수 있음
    assert locks.acquire(agent_lock_key(room_id))
