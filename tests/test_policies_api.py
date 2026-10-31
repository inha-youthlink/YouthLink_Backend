from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.policies import get_policy_service
from app.main import app
from app.pipeline.conditions import RegionIndex
from app.repositories.policy_repository import PolicyFilter, PolicyRecord
from app.services.policy_service import PolicyService
from tests.conftest import HEADERS, REGIONS, FakePolicyRepository

RECORD = PolicyRecord(
    policy_no="P-1",
    policy_name="청년 월세 지원",
    application_period_type_code="0057001",
    application_start_date=date(2026, 1, 1),
    application_end_date=date(2099, 12, 31),
    application_url="www.example.com",
    reference_url_1=None,
    reference_url_2=None,
    supervising_org_name="서울특별시",
    large_categories=["주거"],
)


class SearchPolicyRepository(FakePolicyRepository):
    def __init__(self, records: list[PolicyRecord], total: int) -> None:
        self.records = records
        self.total = total
        self.calls: list[tuple[PolicyFilter, int, int]] = []

    async def search(self, policy_filter, today, offset, limit):
        self.calls.append((policy_filter, offset, limit))
        return self.records, self.total

    async def get_region_labels(self, policy_nos):
        return {"P-1": "서울특별시"}


@pytest.fixture
def repo() -> SearchPolicyRepository:
    return SearchPolicyRepository([RECORD], total=41)


@pytest.fixture
def client(repo):
    app.dependency_overrides[get_policy_service] = lambda: PolicyService(repo)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _search(client, **params):
    return client.get("/api/policies", params=params, headers=HEADERS)


def test_list_regions(client):
    response = client.get("/api/regions", headers=HEADERS)

    assert response.status_code == 200
    # 사용자별 응답이 아님
    assert "cache-control" not in response.headers
    assert response.json() == [
        {
            "code": "11000",
            "name": "서울특별시",
            "sigungu": [
                {"code": "11000", "name": "서울특별시 전체"},
                {"code": "11110", "name": "종로구"},
                {"code": "11680", "name": "강남구"},
            ],
        },
        # 구가 있는 시는 시 하나, 시 코드로
        {
            "code": "41000",
            "name": "경기도",
            "sigungu": [{"code": "41000", "name": "경기도 전체"}, {"code": "41110", "name": "수원시"}],
        },
    ]


# 목록의 코드는 세션 생성에서 모두 받아지고, 1차 필터 범위도 같아야 함
def test_region_codes_are_profile_codes(client):
    regions = RegionIndex(REGIONS)
    groups = client.get("/api/regions", headers=HEADERS).json()

    codes = [option["code"] for group in groups for option in group["sigungu"]]
    assert all(regions.is_profile_code(code) for code in codes)
    assert regions.profile_region("41110").codes == ("41111", "41113")


def test_regions_require_client_id(client):
    response = client.get("/api/regions")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CLIENT_ID"


def test_search_page(client, repo):
    response = _search(client, page=2, size=10)

    assert response.status_code == 200
    assert "cache-control" not in response.headers
    body = response.json()
    assert {key: body[key] for key in ("page", "size", "totalElements", "totalPages")} == {
        "page": 2, "size": 10, "totalElements": 41, "totalPages": 5,
    }
    # 채팅 카드와 같은 형식
    assert body["items"] == [{
        "id": "P-1",
        "title": "청년 월세 지원",
        "category": "HOUSING",
        "applyStart": "2026-01-01",
        "applyEnd": "2099-12-31",
        "status": "OPEN",
        "regionLabel": "서울특별시",
        "region": "SEOUL",
        "organization": "서울특별시",
        "sourceUrl": "https://www.example.com",
    }]
    assert repo.calls == [(PolicyFilter(), 20, 10)]


def test_search_defaults_and_empty_result(client, repo):
    repo.records, repo.total = [], 0

    body = _search(client).json()

    assert body == {"items": [], "page": 0, "size": 20, "totalElements": 0, "totalPages": 0}
    assert repo.calls == [(PolicyFilter(), 0, 20)]


def test_search_keyword_words(client, repo):
    _search(client, keyword="  월세   지원 ")

    assert repo.calls[0][0].keywords == ("월세", "지원")


def test_blank_keyword_means_no_keyword(client, repo):
    response = _search(client, keyword="   ")

    assert response.status_code == 200
    assert repo.calls[0][0].keywords == ()


@pytest.mark.parametrize(("category", "names"), [
    ("HOUSING", ("주거",)),
    # 옛 분류 이름도 함께
    ("WELFARE", ("금융･복지･문화", "복지문화")),
    # 과도기 호환: v0.3 FINANCE는 WELFARE
    ("FINANCE", ("금융･복지･문화", "복지문화")),
])
def test_search_category(client, repo, category, names):
    response = _search(client, category=category)

    assert response.status_code == 200
    assert repo.calls[0][0].large_categories == names


@pytest.mark.parametrize(("region", "sido_code", "codes"), [
    ("11680", "11", ("11680",)),
    ("11000", "11", ()),
    # 구가 있는 시는 시 코드와 구 코드 모두 그 시 전체
    ("41110", "41", ("41111", "41113")),
    ("41113", "41", ("41111", "41113")),
    # 과도기 호환: v0.3 시도 enum
    ("SEOUL", "11", ()),
])
def test_search_region_scope(client, repo, region, sido_code, codes):
    response = _search(client, region=region)

    assert response.status_code == 200
    policy_filter = repo.calls[0][0]
    assert (policy_filter.sido_code, policy_filter.region_codes) == (sido_code, codes)


@pytest.mark.parametrize(("params", "field"), [
    ({"keyword": "가" * 51}, "keyword"),
    ({"category": "FOOD"}, "category"),
    ({"region": "99999"}, "region"),
    ({"region": "11999"}, "region"),
    ({"region": "BUSAN"}, "region"),
    ({"page": -1}, "page"),
    ({"size": 0}, "size"),
    ({"size": 51}, "size"),
    ({"size": "many"}, "size"),
])
def test_search_validation(client, repo, params, field):
    response = _search(client, **params)

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert [detail["field"] for detail in body["details"]] == [field]
    assert repo.calls == []


def test_search_requires_client_id(client):
    response = client.get("/api/policies")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CLIENT_ID"
