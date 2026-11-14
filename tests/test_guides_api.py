import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_guide_store
from app.main import app
from app.services.guide_store import GuideStore


@pytest.fixture
def store(tmp_path) -> GuideStore:
    return GuideStore(tmp_path, 600)


@pytest.fixture
def client(store):
    app.dependency_overrides[get_guide_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


# 브라우저가 링크로 바로 열어 X-Client-Id 없이 받음
def test_get_guide_pdf(client, store):
    saved = asyncio.run(store.save(b"%PDF-1.7 guide", "청년 월세 지원 신청 가이드.pdf"))

    response = client.get(f"/api/guides/{saved.token}.pdf")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.7 guide"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        "inline; filename=\"guide.pdf\"; filename*=UTF-8''"
        "%EC%B2%AD%EB%85%84%20%EC%9B%94%EC%84%B8%20%EC%A7%80%EC%9B%90%20"
        "%EC%8B%A0%EC%B2%AD%20%EA%B0%80%EC%9D%B4%EB%93%9C.pdf"
    )


@pytest.mark.parametrize("path", ["/api/guides/" + "A" * 43 + ".pdf", "/api/guides/short.pdf"])
def test_unknown_guide(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {
        "code": "GUIDE_NOT_FOUND",
        "message": "가이드 링크가 만료되었어요. 가이드를 다시 만들어 주세요.",
    }
