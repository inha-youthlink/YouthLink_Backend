import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.sessions import get_chat_service
from app.core.config import get_settings
from app.core.errors import SessionNotFoundError
from app.core.rate_limit import RateLimits
from app.core.session_lock import SessionLocks
from app.main import app
from app.repositories.policy_repository import PolicyDetailRecord, PolicyRecord
from app.schemas.pipeline import PolicyItem, Profile
from app.schemas.session import ChatRequest
from tests.conftest import (
    CLIENT_ID,
    HEADERS,
    OTHER_CLIENT_ID,
    PROFILE,
    FakeChatRepository,
    FakePipeline,
    FakePolicyRepository,
    make_service,
)

MESSAGE = {"clientMessageId": "5d7f0a3e-2b1c-4e8f-9a6d-3c2b1a0f9e8d", "content": "신청 기간은?"}


def _detail(policy_no: str, *, is_active: bool = True) -> PolicyDetailRecord:
    record = PolicyRecord(
        policy_no=policy_no, policy_name=f"정책 {policy_no}", application_period_type_code=None,
        application_start_date=None, application_end_date=None, application_url=None,
        reference_url_1=None, reference_url_2=None, supervising_org_name=None,
    )
    return PolicyDetailRecord(
        record=record, description=None, support_content=None, application_method=None,
        submission_documents=None, additional_qualification=None, participation_exclusion=None,
        operating_org_name=None, min_age=None, max_age=None, income_condition_code=None,
        min_income=None, max_income=None, income_etc=None, marriage_name=None, is_active=is_active,
    )


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def locks() -> SessionLocks:
    return SessionLocks()


@pytest.fixture
def pipeline(monkeypatch) -> FakePipeline:
    fake = FakePipeline()
    monkeypatch.setattr("app.pipeline.runner.stream_pipeline", fake)
    return fake


@pytest.fixture
def policies() -> FakePolicyRepository:
    return FakePolicyRepository({
        "P-ACCOUNT": _detail("P-ACCOUNT"),
        "P-OTHER": _detail("P-OTHER"),
        "P-GONE": _detail("P-GONE", is_active=False),
    })


@pytest.fixture
def client(repo, locks, pipeline, policies):
    app.dependency_overrides[get_chat_service] = lambda: make_service(repo, locks, policies)
    yield TestClient(app)
    app.dependency_overrides.pop(get_chat_service, None)


def _session_id(client, headers=HEADERS) -> str:
    return client.post("/api/sessions", json={"profile": PROFILE}, headers=headers).json()["id"]


def _put(client, session_id, body, headers=HEADERS):
    return client.put(f"/api/sessions/{session_id}/current-policy", json=body, headers=headers)


# 다음 질문의 분석이 이 정책을 현재 정책으로 받음. 메시지는 만들지 않음 (계약 4.9)
def test_next_question_gets_current_policy(client, repo, pipeline):
    session_id = _session_id(client)

    response = _put(client, session_id, {"policyId": " P-ACCOUNT "})
    messages = client.get(f"/api/sessions/{session_id}/messages", headers=HEADERS).json()
    client.post(f"/api/sessions/{session_id}/chat", json=MESSAGE, headers=HEADERS)

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert messages == []
    assert [policy.policy_no for policy in pipeline.calls[0].current_policies] == ["P-ACCOUNT"]


# 이전 현재 정책(여러 개)을 지우고 그 정책 하나로 바꿈
def test_replaces_previous_current_policies(client, repo):
    session_id = _session_id(client)
    room_id = next(iter(repo.rooms))
    _put(client, session_id, {"policyId": "P-OTHER"})

    _put(client, session_id, {"policyId": "P-ACCOUNT"})

    assert [policy.policy_no for policy in repo.current[room_id]] == ["P-ACCOUNT"]


# 상세 API와 같은 기준: 원천에서 사라진 정책은 없는 정책
@pytest.mark.parametrize("policy_id", ["P-404", "P-GONE"])
def test_unknown_or_inactive_policy(client, repo, policy_id):
    session_id = _session_id(client)

    response = _put(client, session_id, {"policyId": policy_id})

    assert response.status_code == 404
    assert response.json()["code"] == "POLICY_NOT_FOUND"
    assert repo.current == {}


@pytest.mark.parametrize("session_id", ["00000000-0000-4000-8000-000000000000", "not-a-uuid"])
def test_unknown_session(client, session_id):
    response = _put(client, session_id, {"policyId": "P-ACCOUNT"})

    assert response.status_code == 404
    assert response.json()["code"] == "SESSION_NOT_FOUND"


def test_other_users_session(client, repo):
    session_id = _session_id(client, headers={"X-Client-Id": OTHER_CLIENT_ID})

    response = _put(client, session_id, {"policyId": "P-ACCOUNT"})

    assert response.status_code == 404
    assert repo.current == {}


@pytest.mark.parametrize("body", [{}, {"policyId": ""}, {"policyId": "   "}, {"policyId": "P" * 31}])
def test_invalid_policy_id(client, body):
    response = _put(client, _session_id(client), body)

    assert response.status_code == 400
    assert response.json()["details"][0]["field"] == "policyId"


# 채팅 답변 중(잠금 중)에도 지정할 수 있음
def test_allowed_while_answering(client, repo, locks):
    session_id = _session_id(client)
    room_id = next(iter(repo.rooms))
    assert locks.acquire(str(room_id))

    response = _put(client, session_id, {"policyId": "P-ACCOUNT"})

    assert response.status_code == 204


def test_requires_client_id(client):
    session_id = _session_id(client)

    response = _put(client, session_id, {"policyId": "P-ACCOUNT"}, headers={})

    assert response.json()["code"] == "INVALID_CLIENT_ID"


# 카드가 토큰보다 먼저 와 답변 중에도 카드 상세를 열 수 있음. 그때 지정한 정책이 답변 저장에 덮이지 않아야 함
async def test_policy_set_during_answer_survives_answer_save(repo, locks, policies, monkeypatch):
    card = PolicyItem(policy_no="P-RENT", policy_name="청년 월세 지원", score=0.9)
    monkeypatch.setattr("app.pipeline.runner.stream_pipeline", FakePipeline(policies=[card]))
    service = make_service(repo, locks, policies)
    room_id = await repo.create_room(CLIENT_ID, Profile(age=27, region_code="11680"))
    events = await service.start_chat(
        room_id, CLIENT_ID, ChatRequest(client_message_id=uuid.uuid4(), content="월세 지원 뭐 있어?"),
        RateLimits(get_settings()),
    )

    assert (await anext(events)).startswith("event: policies")
    await service.set_current_policy(room_id, CLIENT_ID, "P-ACCOUNT")
    rest = [event async for event in events]

    assert rest[-1].startswith("event: done")
    assert [policy.policy_no for policy in repo.current[room_id]] == ["P-ACCOUNT"]


# 지정 없이 끝난 답변은 그 답변의 현재 정책으로 바뀜 (기존 동작)
async def test_answer_without_set_updates_current_policy(repo, locks, policies, monkeypatch):
    card = PolicyItem(policy_no="P-RENT", policy_name="청년 월세 지원", score=0.9)
    monkeypatch.setattr("app.pipeline.runner.stream_pipeline", FakePipeline(policies=[card]))
    service = make_service(repo, locks, policies)
    room_id = await repo.create_room(CLIENT_ID, Profile(age=27, region_code="11680"))
    await service.set_current_policy(room_id, CLIENT_ID, "P-ACCOUNT")
    events = await service.start_chat(
        room_id, CLIENT_ID, ChatRequest(client_message_id=uuid.uuid4(), content="월세 지원 뭐 있어?"),
        RateLimits(get_settings()),
    )

    [event async for event in events]

    assert [policy.policy_no for policy in repo.current[room_id]] == ["P-RENT"]


# 주인 확인 뒤 방이 지워진 경우
async def test_room_deleted_after_owner_check(repo, locks, policies, monkeypatch):
    service = make_service(repo, locks, policies)
    room_id = await repo.create_room(CLIENT_ID, Profile())

    async def deleted(room_id, policy_nos):
        return False

    monkeypatch.setattr(repo, "replace_current_policies", deleted)

    with pytest.raises(SessionNotFoundError):
        await service.set_current_policy(room_id, CLIENT_ID, "P-ACCOUNT")
