import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.sessions import get_chat_service
from app.core.config import get_settings
from app.core.middleware import SECURITY_HEADERS, BodySizeLimitMiddleware
from app.main import app
from tests.test_sessions_api import HEADERS, PROFILE, FakeChatRepository, make_service

ORIGIN_SECRET = "o" * 43


@pytest.fixture
def client():
    repo = FakeChatRepository()
    app.dependency_overrides[get_chat_service] = lambda: make_service(repo)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _use_settings(**values) -> None:
    settings = get_settings().model_copy(update=values)
    app.dependency_overrides[get_settings] = lambda: settings


@pytest.mark.parametrize("path", ["/health", "/api/sessions", "/api/nope"])
def test_security_headers(client, path):
    response = client.get(path, headers=HEADERS)

    for key, value in SECURITY_HEADERS.items():
        assert response.headers[key] == value


def test_security_headers_keep_sse_headers(client):
    _use_settings(chat_skeleton_first_delay=0, chat_skeleton_tokens=1)
    session_id = client.post("/api/sessions", json={"profile": PROFILE}, headers=HEADERS).json()["id"]

    response = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"clientMessageId": "5d7f0a3e-2b1c-4e8f-9a6d-3c2b1a0f9e8d", "content": "질문"},
        headers=HEADERS,
    )

    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text.endswith("event: done\ndata: {\"messageId\":\"skeleton-5d7f0a3e-2b1c-4e8f-9a6d-3c2b1a0f9e8d\"}\n\n")


def test_too_large_body_on_public_api(client):
    response = client.post("/api/sessions", content=b"x" * (256 * 1024 + 1), headers={
        **HEADERS, "Content-Type": "application/json",
    })

    assert response.status_code == 413
    assert response.json() == {"code": "PAYLOAD_TOO_LARGE", "message": "요청 본문이 너무 큽니다."}
    # 앱 밖에서 거절해도 보안 헤더는 붙음
    assert response.headers["x-content-type-options"] == "nosniff"


def test_too_large_body_on_internal_keeps_fastapi_format(client):
    response = client.post("/internal/pipeline", content=b"x" * (256 * 1024 + 1))

    assert response.status_code == 413
    assert response.json() == {"detail": "요청 본문이 너무 큽니다."}


def _echo_app(max_bytes: int) -> TestClient:
    small = FastAPI()

    @small.post("/api/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    small.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)
    return TestClient(small)


# Content-Length 없이(chunked) 보내도 상한을 넘으면 거절하고, 넘지 않으면 본문이 그대로 앱에 감
def test_chunked_body_limit():
    client = _echo_app(max_bytes=10)

    small = client.post("/api/echo", content=iter([b"12345", b"6789"]))
    large = client.post("/api/echo", content=iter([b"123456", b"789012"]))

    assert small.json() == {"size": 9}
    assert large.status_code == 413


def test_origin_secret_off_by_default(client):
    assert client.get("/api/sessions", headers=HEADERS).status_code == 200


@pytest.mark.parametrize("value", [None, "wrong"])
def test_origin_secret_blocks_direct_requests(client, value):
    _use_settings(origin_secret=SecretStr(ORIGIN_SECRET))
    headers = HEADERS if value is None else {**HEADERS, "X-Origin-Secret": value}

    response = client.get("/api/sessions", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"code": "FORBIDDEN", "message": "허용되지 않은 접근입니다."}


def test_origin_secret_allows_vercel(client):
    _use_settings(origin_secret=SecretStr(ORIGIN_SECRET))

    response = client.get("/api/sessions", headers={**HEADERS, "X-Origin-Secret": ORIGIN_SECRET})

    assert response.status_code == 200


def test_origin_secret_does_not_block_health(client):
    _use_settings(origin_secret=SecretStr(ORIGIN_SECRET))

    assert client.get("/health").status_code == 200
