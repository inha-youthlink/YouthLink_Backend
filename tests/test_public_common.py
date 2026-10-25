from datetime import UTC, datetime

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.dependencies import ClientId
from app.core.errors import SessionNotFoundError, register_error_handlers
from app.schemas.public import KstDateTime, PublicModel

CLIENT_ID = "6f1c2b9e-3d4a-4c8b-9f2e-1a2b3c4d5e6f"


class _Profile(BaseModel):
    age: int = Field(ge=0)


class _Body(PublicModel):
    client_message_id: str
    content: str = Field(min_length=1, max_length=5)
    profile: _Profile | None = None


class _Stamp(PublicModel):
    created_at: KstDateTime


def _make_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    api = APIRouter(prefix="/api")

    @api.get("/me")
    async def me(client_id: ClientId) -> dict[str, str]:
        return {"clientId": client_id}

    @api.post("/echo")
    async def echo(body: _Body) -> _Body:
        return body

    @api.get("/missing")
    async def missing() -> None:
        raise SessionNotFoundError

    @api.get("/boom")
    async def boom() -> None:
        raise RuntimeError("내부 정보")

    @api.get("/stamp")
    async def stamp() -> _Stamp:
        return _Stamp(created_at=datetime(2026, 10, 25, 3, 0, tzinfo=UTC))

    internal = APIRouter(prefix="/internal")

    @internal.post("/echo")
    async def internal_echo(body: _Body) -> _Body:
        return body

    app.include_router(api)
    app.include_router(internal)
    return app


@pytest.fixture
def client() -> TestClient:
    # 예상하지 못한 오류의 응답 형식을 보려고 테스트 클라이언트가 예외를 다시 올리지 않게 함
    return TestClient(_make_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("headers", [{}, {"X-Client-Id": "abc"}, {"X-Client-Id": ""}])
def test_invalid_client_id(client, headers):
    response = client.get("/api/me", headers=headers)

    assert response.status_code == 400
    assert response.json() == {"code": "INVALID_CLIENT_ID", "message": "사용자 식별 정보가 올바르지 않습니다."}


def test_client_id_rejects_other_uuid_versions(client):
    # uuid1(시간 기반)은 계약의 v4가 아님
    response = client.get("/api/me", headers={"X-Client-Id": "6f1c2b9e-3d4a-1c8b-9f2e-1a2b3c4d5e6f"})

    assert response.json()["code"] == "INVALID_CLIENT_ID"


def test_client_id_is_normalized(client):
    response = client.get("/api/me", headers={"X-Client-Id": CLIENT_ID.upper().replace("-", "")})

    assert response.json() == {"clientId": CLIENT_ID}


def test_validation_error_uses_contract_format(client):
    response = client.post("/api/echo", json={"content": "", "profile": {"age": -1}})

    assert response.status_code == 400
    assert response.json() == {
        "code": "VALIDATION_FAILED",
        "message": "입력값을 확인해 주세요.",
        "details": [
            {"field": "clientMessageId", "reason": "필수 항목입니다."},
            {"field": "content", "reason": "1자 이상이어야 합니다."},
            {"field": "profile.age", "reason": "0 이상이어야 합니다."},
        ],
    }


def test_broken_json_body(client):
    response = client.post("/api/echo", content="{", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["details"][0]["field"] == "body"


def test_api_error(client):
    response = client.get("/api/missing")

    assert response.status_code == 404
    assert response.json() == {"code": "SESSION_NOT_FOUND", "message": "세션을 찾을 수 없습니다."}


def test_unknown_path_and_method(client):
    not_found = client.get("/api/nope")
    not_allowed = client.delete("/api/echo")

    assert (not_found.status_code, not_found.json()["code"]) == (404, "NOT_FOUND")
    assert (not_allowed.status_code, not_allowed.json()["code"]) == (405, "METHOD_NOT_ALLOWED")
    assert not_allowed.headers["allow"] == "POST"


def test_unexpected_error_hides_details(client):
    response = client.get("/api/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "내부 정보" not in response.text


def test_internal_keeps_fastapi_format(client):
    # 평가 서버가 쓰는 기존 형식은 그대로 둠
    validation = client.post("/internal/echo", json={})
    not_found = client.get("/internal/nope")

    assert validation.status_code == 422
    assert "detail" in validation.json()
    assert not_found.json() == {"detail": "Not Found"}


def test_datetime_in_kst(client):
    response = client.get("/api/stamp")

    assert response.json() == {"createdAt": "2026-10-25T12:00:00+09:00"}
