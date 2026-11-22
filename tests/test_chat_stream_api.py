import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.sessions import get_chat_service
from app.core.config import get_settings
from app.main import app
from app.schemas.pipeline import PolicyItem
from tests.conftest import (
    CLIENT_ID,
    HEADERS,
    OTHER_CLIENT_ID,
    PROFILE,
    FakeChatRepository,
    FakePipeline,
    make_service,
)

MESSAGE_ID = "5d7f0a3e-2b1c-4e8f-9a6d-3c2b1a0f9e8d"
CARD = PolicyItem(policy_no="P-RENT", policy_name="청년 월세 지원", score=0.9, region_label="서울특별시")
# 10번째 조각에 줄바꿈을 넣어 JSON 문자열로 나가는지 봄
TOKENS = [f"답변 {n}. " for n in range(1, 10)] + ["줄바꿈\n", "11. ", "12."]


@pytest.fixture
def client(monkeypatch):
    repo = FakeChatRepository()
    # 첫 이벤트 전 대기가 ping 간격보다 길어 :ping이 먼저 나감
    monkeypatch.setattr(
        "app.pipeline.runner.stream_pipeline", FakePipeline(tokens=TOKENS, policies=[CARD], delay=0.12)
    )
    settings = get_settings().model_copy(update={"sse_ping_interval": 0.05})
    app.dependency_overrides[get_chat_service] = lambda: make_service(repo)
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def _session_id(client, headers=HEADERS) -> str:
    return client.post("/api/sessions", json={"profile": PROFILE}, headers=headers).json()["id"]


def _chat(client, session_id, body=None, headers=HEADERS):
    body = body if body is not None else {"clientMessageId": MESSAGE_ID, "content": "서울 월세 지원 알려줘"}
    return client.post(f"/api/sessions/{session_id}/chat", json=body, headers=headers)


# 빈 줄로 이벤트를 나누고, :ping은 ("ping", None)으로 둠
def _events(text: str) -> list[tuple[str, object]]:
    events = []
    for block in text.split("\n\n"):
        if not block:
            continue
        if block == ":ping":
            events.append(("ping", None))
            continue
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_stream_follows_contract_order(client):
    response = _chat(client, _session_id(client))

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    events = _events(response.text)
    names = [name for name, _ in events]
    # 첫 이벤트 전 대기 동안 :ping이 나가고, policies → token들 → done (계약 5.2)
    assert names[0] == "ping"
    content = [name for name in names if name != "ping"]
    assert content == ["policies", *["token"] * 12, "done"]
    assert events[-1][1]["messageId"].isdigit()
    assert [card["id"] for card in dict(events)["policies"]] == ["P-RENT"]


def test_token_with_newline_stays_json_string(client):
    events = _events(_chat(client, _session_id(client)).text)

    tokens = [data for name, data in events if name == "token"]
    assert tokens[9].endswith("\n")
    assert all(isinstance(token, str) for token in tokens)


def test_retry_with_same_client_message_id_gets_same_done_id(client):
    session_id = _session_id(client)

    first = _events(_chat(client, session_id).text)[-1]
    second = _events(_chat(client, session_id).text)[-1]

    assert first == second


def test_other_users_session_returns_json_404(client):
    session_id = _session_id(client, headers={"X-Client-Id": OTHER_CLIENT_ID})

    response = _chat(client, session_id)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["code"] == "SESSION_NOT_FOUND"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"clientMessageId": MESSAGE_ID, "content": ""}, "content"),
        ({"clientMessageId": MESSAGE_ID, "content": "가" * 1001}, "content"),
        ({"clientMessageId": "abc", "content": "질문"}, "clientMessageId"),
        ({"content": "질문"}, "clientMessageId"),
    ],
)
def test_invalid_body_returns_json_400(client, body, field):
    response = _chat(client, _session_id(client), body=body)

    assert response.status_code == 400
    assert response.json()["details"][0]["field"] == field


def test_requires_client_id(client):
    session_id = _session_id(client)

    response = _chat(client, session_id, headers={})

    assert response.json()["code"] == "INVALID_CLIENT_ID"


def test_unknown_session(client):
    response = _chat(client, str(uuid.uuid4()))

    assert response.status_code == 404


def test_session_owner_header_is_case_insensitive(client):
    session_id = _session_id(client)

    response = _chat(client, session_id, headers={"X-Client-Id": CLIENT_ID.upper()})

    assert response.status_code == 200
