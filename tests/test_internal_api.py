import pytest
from pydantic import SecretStr
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.dependencies import get_pipeline_deps
from app.main import app
from app.schemas.pipeline import PipelineOutput

TRACE = {
    "handler": "search",
    "conditions": {"filtered_policy_count": 2},
    "contexts": ["근거"],
    "retrieved_chunks": [],
    "filtered_policy_nos": ["P-A", "P-B"],
}


async def _fake_pipeline(inp, deps):
    return PipelineOutput(answer="답변", policies=[], trace=dict(TRACE))


INTERNAL_KEY = "k" * 43


def _use_key(key):
    settings = get_settings().model_copy(update={"internal_api_key": SecretStr(key) if key else None})
    app.dependency_overrides[get_settings] = lambda: settings


# 개발자 .env에 키가 있어도 결과가 같게 기본은 키 없음(로컬)으로 고정함
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.api.internal.run_pipeline", _fake_pipeline)
    app.dependency_overrides[get_pipeline_deps] = lambda: None
    _use_key(None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_debug_keeps_heavy_trace(client):
    response = client.post("/internal/pipeline?debug=true", json={"question": "q"})

    assert response.json()["trace"] == TRACE


def test_default_trace_drops_debug_only_keys(client):
    response = client.post("/internal/pipeline", json={"question": "q"})

    # 정책 번호 목록과 근거는 debug 요청에만 실음
    assert response.json()["trace"] == {"handler": "search", "conditions": {"filtered_policy_count": 2}}


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Key": "wrong"}, {"X-Internal-Key": ""}])
def test_internal_key_required_when_set(client, headers):
    _use_key(INTERNAL_KEY)

    response = client.post("/internal/pipeline", json={"question": "q"}, headers=headers)

    # 평가 서버가 쓰는 FastAPI 기본 형식
    assert response.status_code == 401
    assert response.json() == {"detail": "내부 API 키가 올바르지 않습니다."}


def test_internal_key_accepted(client):
    _use_key(INTERNAL_KEY)

    response = client.post("/internal/pipeline", json={"question": "q"}, headers={"X-Internal-Key": INTERNAL_KEY})

    assert response.status_code == 200


def test_internal_key_checked_before_body(client):
    _use_key(INTERNAL_KEY)

    # 키가 없으면 본문 검증 결과도 알려 주지 않음
    response = client.post("/internal/pipeline", json={})

    assert response.status_code == 401


def test_public_api_does_not_need_internal_key(client):
    _use_key(INTERNAL_KEY)

    response = client.get("/api/sessions")

    assert response.json()["code"] == "INVALID_CLIENT_ID"
