import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.api.internal.run_pipeline", _fake_pipeline)
    app.dependency_overrides[get_pipeline_deps] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_debug_keeps_heavy_trace(client):
    response = client.post("/internal/pipeline?debug=true", json={"question": "q"})

    assert response.json()["trace"] == TRACE


def test_default_trace_drops_debug_only_keys(client):
    response = client.post("/internal/pipeline", json={"question": "q"})

    # 정책 번호 목록과 근거는 debug 요청에만 실음
    assert response.json()["trace"] == {"handler": "search", "conditions": {"filtered_policy_count": 2}}
