import json
import uuid

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError

from app.api.sessions import get_chat_service
from app.core.config import get_settings
from app.core.rate_limit import RateLimits
from app.core.session_lock import SessionLocks
from app.llm.client import LLMStreamError
from app.main import app
from app.schemas.pipeline import PolicyItem, Profile
from app.schemas.session import ChatRequest
from tests.conftest import (
    CLIENT_ID,
    HEADERS,
    PROFILE,
    FakeChatRepository,
    FakePipeline,
    make_service,
)

KEY = "5d7f0a3e-2b1c-4e8f-9a6d-3c2b1a0f9e8d"
OTHER_KEY = "8a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
CARD = PolicyItem(policy_no="P-RENT", policy_name="청년 월세 지원", score=0.9, region_label="서울특별시")


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def locks() -> SessionLocks:
    return SessionLocks()


@pytest.fixture
def pipeline(monkeypatch) -> FakePipeline:
    fake = FakePipeline(tokens=["청년 ", "월세 ", "지원"], policies=[CARD])
    monkeypatch.setattr("app.pipeline.runner.stream_pipeline", fake)
    return fake


@pytest.fixture
def client(repo, locks, pipeline):
    settings = get_settings().model_copy(update={"chat_skeleton_enabled": False, "sse_ping_interval": 5.0})
    app.dependency_overrides[get_chat_service] = lambda: make_service(repo, locks)
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.pop(get_chat_service, None)
    app.dependency_overrides.pop(get_settings, None)


def _session_id(client) -> str:
    return client.post("/api/sessions", json={"profile": PROFILE}, headers=HEADERS).json()["id"]


def _chat(client, session_id, key=KEY, content="서울 월세 지원 알려줘"):
    return client.post(
        f"/api/sessions/{session_id}/chat", json={"clientMessageId": key, "content": content}, headers=HEADERS
    )


def _events(text: str) -> list[tuple[str, object]]:
    events = []
    for block in text.split("\n\n"):
        if block and block != ":ping":
            lines = dict(line.split(": ", 1) for line in block.split("\n"))
            events.append((lines["event"], json.loads(lines["data"])))
    return events


# 카드 → 본문 조각 → done 순서로 보내고, 질문은 먼저·답변은 끝난 뒤 저장함
def test_new_question_streams_and_saves(client, repo, pipeline):
    session_id = _session_id(client)

    response = _chat(client, session_id, content="  서울 월세 지원 알려줘 ")

    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _ in events] == ["policies", "token", "token", "token", "done"]
    assert events[0][1][0]["id"] == "P-RENT"
    assert "".join(data for name, data in events if name == "token") == "청년 월세 지원"

    room_id = uuid.UUID(session_id)
    question, answer = repo.messages[room_id]
    # 질문은 앞뒤 공백을 지운 값으로 저장
    assert (question.role, question.content, str(question.client_message_id)) == ("user", "서울 월세 지원 알려줘", KEY)
    assert (answer.role, answer.content) == ("assistant", "청년 월세 지원")
    assert repo.reply_to[answer.message_id] == question.message_id
    assert events[-1][1] == {"messageId": str(answer.message_id)}
    assert [p.policy_no for p in repo.refs[answer.message_id]] == ["P-RENT"]
    assert [p.policy_no for p in repo.current[room_id]] == ["P-RENT"]
    assert repo.metadata[answer.message_id]["chat_model"] == "fake"
    assert repo.rooms[room_id].title == "서울 월세 지원 알려줘"
    # 파이프라인에는 채팅방 프로필(DB 코드)이 들어감
    assert pipeline.calls[0].profile.region_code == "11680"


# 카드가 없는 답변(인사, 조건 되묻기 등)은 policies를 보내지 않음
def test_answer_without_cards_has_no_policies_event(client, pipeline):
    pipeline.policies = []

    events = _events(_chat(client, _session_id(client)).text)

    assert [name for name, _ in events] == ["token", "token", "token", "done"]


# done을 못 받고 같은 키로 다시 보내면 새로 만들지 않고 저장된 답변을 일반 답변과 같은 순서로 보냄
def test_retry_after_answer_replays_saved_answer(client, repo, pipeline):
    session_id = _session_id(client)
    first = _events(_chat(client, session_id).text)

    second = _events(_chat(client, session_id).text)

    assert len(pipeline.calls) == 1
    assert [name for name, _ in second] == ["policies", "token", "done"]
    assert second[0][1] == first[0][1]
    assert second[1][1] == "청년 월세 지원"
    assert second[-1] == first[-1]
    assert len(repo.messages[uuid.UUID(session_id)]) == 2


# 생성이 실패해 질문만 남았으면, 같은 키로 다시 보낼 때 질문은 다시 저장하지 않고 답변만 만듦
def test_retry_after_failure_generates_answer_only(client, repo, pipeline):
    session_id = _session_id(client)
    pipeline.fail = LLMStreamError("실패")
    failed = _events(_chat(client, session_id).text)
    pipeline.fail = None

    retried = _events(_chat(client, session_id).text)

    assert failed[-1][0] == "error"
    assert retried[-1][0] == "done"
    assert [m.role for m in repo.messages[uuid.UUID(session_id)]] == ["user", "assistant"]


def test_same_key_with_different_question_conflicts(client, pipeline):
    session_id = _session_id(client)
    _chat(client, session_id)

    response = _chat(client, session_id, content="다른 질문")

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/json"
    assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(pipeline.calls) == 1


# 같은 세션에서 답변을 만드는 중이면 다른 키로 보내도 막음
def test_session_busy_returns_409(client, locks, pipeline):
    session_id = _session_id(client)
    locks.acquire(session_id)

    response = _chat(client, session_id, key=OTHER_KEY)

    assert response.status_code == 409
    assert response.json()["code"] == "STREAM_IN_PROGRESS"
    assert pipeline.calls == []


# 끝나거나 실패하거나 시작 전에 거절돼도 잠금이 풀려 다음 질문을 받음
def test_lock_released_after_success_failure_and_rejection(client, pipeline):
    session_id = _session_id(client)
    assert _chat(client, session_id).status_code == 200
    assert _chat(client, session_id, content="다른 질문").status_code == 409

    pipeline.fail = RuntimeError("실패")
    assert _events(_chat(client, session_id, key=OTHER_KEY, content="질문2").text)[-1][0] == "error"

    pipeline.fail = None
    assert _events(_chat(client, session_id, key=str(uuid.uuid4()), content="질문3").text)[-1][0] == "done"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (APITimeoutError(request=httpx2.Request("POST", "https://llm.test")), "RAG_TIMEOUT"),
        (LLMStreamError("실패"), "RAG_UPSTREAM_ERROR"),
        (RuntimeError("실패"), "INTERNAL_ERROR"),
    ],
)
def test_failure_during_stream_sends_error_event(client, repo, pipeline, error, code):
    pipeline.fail = error
    session_id = _session_id(client)

    events = _events(_chat(client, session_id).text)

    # 이미 보낸 조각 뒤에 error로 끝나고 done은 없음. 미완성 답변은 저장하지 않음
    assert [name for name, _ in events][-2:] == ["token", "error"]
    assert events[-1][1]["code"] == code
    assert "실패" not in events[-1][1]["message"]
    assert [m.role for m in repo.messages[uuid.UUID(session_id)]] == ["user"]


# 연결이 끊기면 파이프라인을 닫고, 질문만 남기고, 잠금을 풂
async def test_disconnect_closes_pipeline_and_releases_lock(repo, locks, pipeline):
    service = make_service(repo, locks)
    room_id = await repo.create_room(CLIENT_ID, Profile(age=27, region_code="11680"))
    events = await service.start_chat(
        room_id, CLIENT_ID, ChatRequest(client_message_id=uuid.UUID(KEY), content="질문"),
        RateLimits(get_settings().model_copy(update={"llm_daily_limit": 0})),
    )

    assert (await anext(events)).startswith("event: policies")
    await events.aclose()

    assert pipeline.closed
    assert [m.role for m in repo.messages[room_id]] == ["user"]
    assert locks.acquire(str(room_id))


# 대화 기록에는 답변까지 있는 턴만 들어감
def test_history_skips_unanswered_question(client, pipeline):
    session_id = _session_id(client)
    _chat(client, session_id, key=KEY, content="질문1")
    pipeline.fail = LLMStreamError("실패")
    _chat(client, session_id, key=OTHER_KEY, content="끊긴 질문")
    pipeline.fail = None

    _chat(client, session_id, key=str(uuid.uuid4()), content="질문3")

    assert [turn.question for turn in pipeline.calls[-1].history] == ["질문1"]


def test_messages_include_client_message_id(client):
    session_id = _session_id(client)
    _chat(client, session_id)

    messages = client.get(f"/api/sessions/{session_id}/messages", headers=HEADERS).json()

    assert [(m["role"], m["clientMessageId"]) for m in messages] == [("user", KEY), ("assistant", None)]


def test_blank_question_rejected(client):
    response = _chat(client, _session_id(client), content=" \n ")

    assert response.status_code == 400
    assert response.json()["details"] == [{"field": "content", "reason": "1자 이상이어야 합니다."}]
