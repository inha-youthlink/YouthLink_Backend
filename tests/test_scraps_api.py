from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.scraps import get_scrap_service
from app.main import app
from app.repositories.policy_repository import PolicyRecord
from app.repositories.scrap_repository import AddResult, ScrapRecord
from app.services.scrap_service import ScrapService
from tests.conftest import HEADERS, OTHER_CLIENT_ID

SCRAPPED_AT = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


def _record(policy_no: str) -> PolicyRecord:
    return PolicyRecord(
        policy_no=policy_no,
        policy_name=f"정책 {policy_no}",
        application_period_type_code="0057002",
        application_start_date=None,
        application_end_date=None,
        application_url=None,
        reference_url_1=None,
        reference_url_2=None,
        supervising_org_name="서울특별시",
        large_categories=["주거"],
    )


# 메모리 저장소. 실제 SQL(정렬, 비활성, 동시 추가)은 test_scrap_repository가 테스트 DB로 확인함
class FakeScrapRepository:
    def __init__(self, active: set[str]) -> None:
        self.active = active
        self.rows: dict[tuple[str, str], datetime] = {}
        # 추가 직후 비활성이 되는 정책 (추가와 조회 사이의 동기화)
        self.deactivate_after_add: set[str] = set()

    async def list_scraps(self, user_id, today):
        return [ScrapRecord(_record(no), at) for (user, no), at in self.rows.items() if user == user_id and no in self.active]

    async def get_scrap(self, user_id, policy_no, today):
        at = self.rows.get((user_id, policy_no))
        return ScrapRecord(_record(policy_no), at) if at and policy_no in self.active else None

    async def add_scrap(self, user_id, policy_no):
        if policy_no not in self.active:
            return AddResult.POLICY_NOT_FOUND
        if (user_id, policy_no) in self.rows:
            return AddResult.EXISTS
        self.rows[(user_id, policy_no)] = SCRAPPED_AT
        if policy_no in self.deactivate_after_add:
            self.active.discard(policy_no)
        return AddResult.CREATED

    async def remove_scrap(self, user_id, policy_no):
        self.rows.pop((user_id, policy_no), None)


class FakeLabels:
    async def get_region_labels(self, policy_nos):
        return {no: "서울특별시" for no in policy_nos}


@pytest.fixture
def repo() -> FakeScrapRepository:
    return FakeScrapRepository(active={"P-1", "P-2"})


@pytest.fixture
def client(repo):
    app.dependency_overrides[get_scrap_service] = lambda: ScrapService(repo, FakeLabels())
    yield TestClient(app)
    app.dependency_overrides.clear()


def _add(client, policy_id, headers=HEADERS):
    return client.post("/api/scraps", json={"policyId": policy_id}, headers=headers)


def test_add_scrap_created_then_existing(client):
    first = _add(client, "P-1")
    second = _add(client, "P-1")

    assert first.status_code == 201
    assert second.status_code == 200
    # 이미 있으면 기존 값 그대로 (계약 멱등)
    assert first.json() == second.json()
    body = first.json()
    assert body["createdAt"] == "2026-11-01T12:00:00+09:00"
    # 카드는 채팅·검색과 같은 형식
    assert body["policy"] == {
        "id": "P-1", "title": "정책 P-1", "category": "HOUSING", "applyStart": None, "applyEnd": None,
        "status": "ALWAYS_OPEN", "regionLabel": "서울특별시", "region": "SEOUL",
        "organization": "서울특별시", "sourceUrl": None,
    }


@pytest.mark.parametrize("policy_id", ["P-404", "P-GONE"])
def test_add_unknown_or_inactive_policy(client, policy_id):
    response = _add(client, policy_id)

    assert response.status_code == 404
    assert response.json()["code"] == "POLICY_NOT_FOUND"


# 추가와 조회 사이에 정책이 비활성이 되면 목록에서도 빠지므로 없는 정책과 같게
def test_add_policy_deactivated_meanwhile(client, repo):
    repo.deactivate_after_add.add("P-2")

    response = _add(client, "P-2")

    assert response.status_code == 404
    assert response.json()["code"] == "POLICY_NOT_FOUND"


@pytest.mark.parametrize(("body", "field"), [
    ({}, "policyId"),
    ({"policyId": "   "}, "policyId"),
    ({"policyId": "1" * 31}, "policyId"),
    ({"policyId": 123}, "policyId"),
])
def test_add_validation(client, repo, body, field):
    response = client.post("/api/scraps", json=body, headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert [d["field"] for d in response.json()["details"]] == [field]
    assert repo.rows == {}


def test_add_strips_policy_id(client, repo):
    assert _add(client, " P-1 ").status_code == 201
    assert list(repo.rows) == [(HEADERS["X-Client-Id"], "P-1")]


def test_list_scraps_per_client(client):
    _add(client, "P-1")
    _add(client, "P-2", headers={"X-Client-Id": OTHER_CLIENT_ID})

    response = client.get("/api/scraps", headers=HEADERS)

    assert response.status_code == 200
    # 사용자별 응답
    assert response.headers["cache-control"] == "no-store"
    assert [s["policy"]["id"] for s in response.json()] == ["P-1"]


def test_list_scraps_empty(client):
    assert client.get("/api/scraps", headers=HEADERS).json() == []


def test_remove_scrap_idempotent(client, repo):
    _add(client, "P-1")

    first = client.delete("/api/scraps/P-1", headers=HEADERS)
    second = client.delete("/api/scraps/P-1", headers=HEADERS)
    # 형식이 틀린 id도 그대로 204
    odd = client.delete("/api/scraps/" + "x" * 50, headers=HEADERS)

    assert (first.status_code, second.status_code, odd.status_code) == (204, 204, 204)
    assert first.content == b""
    assert repo.rows == {}


def test_remove_scrap_only_own(client, repo):
    _add(client, "P-1", headers={"X-Client-Id": OTHER_CLIENT_ID})

    client.delete("/api/scraps/P-1", headers=HEADERS)

    assert list(repo.rows) == [(OTHER_CLIENT_ID, "P-1")]


@pytest.mark.parametrize(("method", "path"), [("get", "/api/scraps"), ("post", "/api/scraps"), ("delete", "/api/scraps/P-1")])
def test_scraps_require_client_id(client, method, path):
    response = client.request(method, path, json={"policyId": "P-1"} if method == "post" else None)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CLIENT_ID"
