from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.sessions import get_chat_service
from app.core.config import get_settings
from app.core.dependencies import get_pipeline_deps, get_rate_limits
from app.core.rate_limit import DailyLimiter, RateLimits, WindowLimiter
from app.main import app
from app.schemas.pipeline import PipelineOutput
from app.schemas.policy_info import KST
from tests.conftest import (
    HEADERS,
    OTHER_CLIENT_ID,
    PROFILE,
    FakeChatRepository,
    FakePipeline,
    make_service,
)

MESSAGE = {"clientMessageId": "5d7f0a3e-2b1c-4e8f-9a6d-3c2b1a0f9e8d", "content": "질문"}
OTHER_MESSAGE_ID = "8a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_window_limiter_blocks_until_window_ends():
    clock = FakeClock()
    limiter = WindowLimiter(limit=2, window=60, clock=clock)

    assert limiter.hit("a") is None
    assert limiter.hit("a") is None
    clock.now += 20
    assert limiter.hit("a") == 40
    # 다른 키는 따로 셈
    assert limiter.hit("b") is None

    clock.now += 40
    assert limiter.hit("a") is None


def test_window_limiter_zero_means_off():
    limiter = WindowLimiter(limit=0)

    assert all(limiter.hit("a") is None for _ in range(1000))


def test_window_limiter_sweeps_old_keys(monkeypatch):
    monkeypatch.setattr("app.core.rate_limit._SWEEP_SIZE", 3)
    clock = FakeClock()
    limiter = WindowLimiter(limit=5, window=60, clock=clock)
    for key in ("a", "b", "c"):
        limiter.hit(key)

    clock.now += 61
    limiter.hit("d")

    # 지난 구간의 키는 메모리에서 지움
    assert set(limiter._counts) == {"d"}


def test_daily_limiter_resets_at_kst_midnight():
    now = datetime(2026, 10, 26, 23, 0, tzinfo=KST)
    limiter = DailyLimiter(limit=1, now=lambda: now)

    assert limiter.hit() is None
    assert limiter.hit() == 3600

    now = datetime(2026, 10, 27, 0, 0, 1, tzinfo=KST)
    assert limiter.hit() is None


def _request(headers: dict[str, str], client_host: str = "10.0.0.1") -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (client_host, 1234)})


@pytest.mark.parametrize(
    ("trust", "headers", "expected"),
    [
        (True, {"X-Forwarded-For": "1.2.3.4, 10.0.0.9"}, "1.2.3.4"),
        (True, {}, "10.0.0.1"),
        (False, {"X-Forwarded-For": "1.2.3.4"}, "10.0.0.1"),
    ],
)
def test_client_ip(trust, headers, expected):
    limits = RateLimits(get_settings().model_copy(update={"trust_proxy_headers": trust}))

    assert limits.client_ip(_request(headers)) == expected


@pytest.fixture
def limited():
    def make(**values) -> TestClient:
        settings = get_settings().model_copy(update={
            "rate_limit_per_minute": 0,
            "chat_rate_limit_per_minute": 0,
            "chat_client_rate_limit_per_minute": 0,
            "llm_daily_limit": 0,
            "internal_api_key": None,
            "chat_skeleton_enabled": True,
            "chat_skeleton_first_delay": 0,
            "chat_skeleton_tokens": 1,
            **values,
        })
        limits = RateLimits(settings)
        repo = FakeChatRepository()
        app.dependency_overrides[get_rate_limits] = lambda: limits
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_chat_service] = lambda: make_service(repo)
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def _new_session(client, headers=HEADERS) -> str:
    return client.post("/api/sessions", json={"profile": PROFILE}, headers=headers).json()["id"]


def test_public_api_limit_per_ip(limited):
    client = limited(rate_limit_per_minute=2)
    client.get("/api/sessions", headers=HEADERS)
    client.get("/api/sessions", headers=HEADERS)

    response = client.get("/api/sessions", headers=HEADERS)

    assert response.status_code == 429
    assert response.json() == {"code": "RATE_LIMITED", "message": "요청이 많아요. 잠시 후 다시 시도해 주세요."}
    assert 1 <= int(response.headers["retry-after"]) <= 60


def test_ips_behind_proxy_are_counted_separately(limited):
    client = limited(rate_limit_per_minute=1)

    first = client.get("/api/sessions", headers={**HEADERS, "X-Forwarded-For": "1.1.1.1"})
    second = client.get("/api/sessions", headers={**HEADERS, "X-Forwarded-For": "2.2.2.2"})

    assert (first.status_code, second.status_code) == (200, 200)


async def _fake_pipeline(inp, deps):
    return PipelineOutput(answer="답변", policies=[], trace={})


def test_health_and_internal_are_not_limited(limited, monkeypatch):
    monkeypatch.setattr("app.api.internal.run_pipeline", _fake_pipeline)
    client = limited(rate_limit_per_minute=1)
    app.dependency_overrides[get_pipeline_deps] = lambda: None

    for _ in range(3):
        assert client.get("/health").status_code == 200
        assert client.post("/internal/pipeline", json={"question": "q"}).status_code == 200


def test_chat_limit_per_client(limited):
    client = limited(chat_client_rate_limit_per_minute=1)
    session_id = _new_session(client)
    other_session_id = _new_session(client, headers={"X-Client-Id": OTHER_CLIENT_ID})

    first = client.post(f"/api/sessions/{session_id}/chat", json=MESSAGE, headers=HEADERS)
    second = client.post(f"/api/sessions/{session_id}/chat", json=MESSAGE, headers=HEADERS)
    other = client.post(f"/api/sessions/{other_session_id}/chat", json=MESSAGE, headers={"X-Client-Id": OTHER_CLIENT_ID})

    assert (first.status_code, second.status_code, other.status_code) == (200, 429, 200)


def test_llm_daily_limit(limited, monkeypatch):
    monkeypatch.setattr("app.pipeline.runner.stream_pipeline", FakePipeline())
    client = limited(llm_daily_limit=1, chat_skeleton_enabled=False)
    session_id = _new_session(client)

    # 없는 세션처럼 시작 전에 거절된 요청은 세지 않음
    missing = client.post("/api/sessions/00000000-0000-4000-8000-000000000000/chat", json=MESSAGE, headers=HEADERS)
    first = client.post(f"/api/sessions/{session_id}/chat", json=MESSAGE, headers=HEADERS)
    # 저장된 답변을 다시 보내는 요청은 LLM을 부르지 않아 세지 않음
    replay = client.post(f"/api/sessions/{session_id}/chat", json=MESSAGE, headers=HEADERS)
    second = client.post(
        f"/api/sessions/{session_id}/chat", json={**MESSAGE, "clientMessageId": OTHER_MESSAGE_ID}, headers=HEADERS
    )

    assert (missing.status_code, first.status_code, replay.status_code, second.status_code) == (404, 200, 200, 429)
    assert second.json()["message"] == "오늘 이용량이 많아 채팅을 잠시 멈췄어요. 내일 다시 이용해 주세요."
    assert second.headers["content-type"] == "application/json"


# 뼈대는 LLM을 부르지 않아 하루 상한에 세지 않음
def test_skeleton_does_not_count_llm(limited):
    client = limited(llm_daily_limit=1)
    session_id = _new_session(client)

    for message_id in (MESSAGE["clientMessageId"], OTHER_MESSAGE_ID):
        response = client.post(
            f"/api/sessions/{session_id}/chat", json={**MESSAGE, "clientMessageId": message_id}, headers=HEADERS
        )
        assert response.status_code == 200
