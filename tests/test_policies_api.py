import asyncio
import uuid
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.policies import get_policy_service
from app.main import app
from app.pipeline.conditions import RegionIndex
from app.repositories.policy_repository import (
    PolicyDetailRecord,
    PolicyFilter,
    PolicyRecord,
    RegionRow,
)
from app.schemas.pipeline import Profile
from app.schemas.policy import RegionOption
from app.services.policy_service import PolicyService
from tests.conftest import (
    CLIENT_ID,
    HEADERS,
    OTHER_CLIENT_ID,
    REGIONS,
    FakeChatRepository,
    FakeCodes,
    FakePolicyRepository,
)

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

DETAIL = PolicyDetailRecord(
    record=RECORD,
    description=" 월세를 지원합니다. ",
    support_content="월 20만 원",
    application_method="온라인 신청",
    submission_documents="  ",
    additional_qualification=None,
    participation_exclusion=None,
    operating_org_name="서울청년센터",
    min_age=19,
    max_age=39,
    income_condition_code="0043001",
    min_income=None,
    max_income=None,
    income_etc=None,
    marriage_name="제한없음",
)


class SearchPolicyRepository(FakePolicyRepository):
    def __init__(self, records: list[PolicyRecord], total: int, regions: list[RegionRow] = REGIONS) -> None:
        self.records = records
        self.total = total
        self.regions = regions
        self.calls: list[tuple[PolicyFilter, int, int]] = []
        self.detail_calls: list[str] = []

    async def list_regions(self) -> list[RegionRow]:
        return self.regions

    async def search(self, policy_filter, today, offset, limit):
        self.calls.append((policy_filter, offset, limit))
        return self.records, self.total

    async def get_region_labels(self, policy_nos):
        return {"P-1": "서울특별시"}

    async def get_detail(self, policy_no):
        self.detail_calls.append(policy_no)
        return {"P-1": DETAIL, "P-GONE": replace(DETAIL, is_active=False)}.get(policy_no)


@pytest.fixture
def repo() -> SearchPolicyRepository:
    return SearchPolicyRepository([RECORD], total=41)


@pytest.fixture
def chats() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def codes() -> FakeCodes:
    return FakeCodes()


@pytest.fixture
def client(repo, chats, codes):
    app.dependency_overrides[get_policy_service] = lambda: PolicyService(repo, chats, codes)
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


# 구가 있는 시가 여럿이고, 구 없는 시·시도 자체인 시군구(세종)도 섞인 지역 표
MIXED_REGIONS = [
    *REGIONS,
    RegionRow("41", "경기도", "성남시 수정구", "41131"),
    RegionRow("41", "경기도", "성남시 분당구", "41135"),
    RegionRow("41", "경기도", "부천시", "41190"),
    RegionRow("41", "경기도", "고양시 덕양구", "41281"),
    RegionRow("41", "경기도", "고양시 일산동구", "41285"),
    RegionRow("36", "세종특별자치시", "세종특별자치시", "36110"),
    RegionRow("28", "인천광역시", "부평구", "28237"),
    RegionRow("26", "부산광역시", "중구", "26110"),
    # 순서 목록에 없는 새 시도
    RegionRow("99", "새특별자치도", "새시", "99110"),
]


# 목록의 코드는 세션 생성에서 받아지고, 그 코드로 거른 범위가 목록 항목의 시군구와 같아야 함
async def test_region_codes_round_trip():
    regions = RegionIndex(MIXED_REGIONS)
    service = PolicyService(SearchPolicyRepository([], 0, MIXED_REGIONS), FakeChatRepository(), FakeCodes())
    groups = await service.list_regions()

    options = {option.code: option.name for group in groups for option in group.sigungu}
    assert all(regions.is_profile_code(code) for code in options)
    for unit in regions.units:
        if unit.label != unit.sido_name:
            assert regions.profile_region(RegionIndex.unit_code(unit)) == unit
    assert {code: options[code] for code in ("41110", "41130", "41190", "41280")} == {
        "41110": "수원시", "41130": "성남시", "41190": "부천시", "41280": "고양시",
    }
    # 수도권 먼저, 그 뒤는 행정 표준 순서, 모르는 시도는 맨 뒤
    assert [group.code for group in groups] == ["11000", "41000", "28000", "26000", "36000", "99000"]
    # 세종은 "전체" 하나
    assert next(group.sigungu for group in groups if group.code == "36000") == [
        RegionOption(code="36000", name="세종특별자치시 전체")
    ]


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


@pytest.mark.parametrize("params", [{"keyword": "   "}, {"keyword": ""}, {"category": ""}, {"region": ""}])
def test_blank_values_mean_no_condition(client, repo, params):
    response = _search(client, **params)

    assert response.status_code == 200
    assert repo.calls == [(PolicyFilter(), 0, 20)]


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


PROFILE = Profile(
    age=27, region_code="11680", marriage_status_code="0055002", annual_income=30_000_000,
    major_code="0011005", job_code="0013003", school_code="0049007", special_target_code="0014010",
)


@pytest.fixture
def session_id(chats) -> uuid.UUID:
    return asyncio.run(chats.create_room(CLIENT_ID, PROFILE))


# 채팅방 안 검색: 지역 대신 세션 프로필로 1차 필터 (채팅·추천과 같은 조건) + 검색어·카테고리
def test_session_search_uses_profile_filter(client, repo, session_id):
    response = _search(client, sessionId=str(session_id), keyword="월세 지원", category="HOUSING", page=1, size=10)

    assert response.status_code == 200
    # 세션 프로필마다 결과가 달라 공용 캐시에 남기지 않음
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["items"][0]["id"] == "P-1"
    policy_filter, offset, limit = repo.calls[0]
    assert (offset, limit) == (10, 10)
    assert policy_filter == PolicyFilter(
        sido_code="11",
        region_codes=("11680",),
        age=27,
        eligibility_codes={"JOB": "0013003", "SCHOOL": "0049007", "MAJOR": "0011005", "SPECIAL": "0014010"},
        marriage_code="0055002",
        annual_income_won=30_000_000,
        # 정책 검색과 같게 마감·예정도 남김 (추천과 다름)
        exclude_closed=False,
        exclude_upcoming=False,
        large_categories=("주거",),
        keywords=("월세", "지원"),
    )


# 프로필 "기타" 코드는 채팅·추천처럼 거르지 않음
def test_session_search_other_code_not_filtered(client, repo, codes, session_id):
    codes.names["0013003"] = "기타"

    _search(client, sessionId=str(session_id))

    assert "JOB" not in repo.calls[0][0].eligibility_codes


# 빈 값은 조건 없음: 빈 sessionId는 지금 검색 그대로, 빈 region은 세션과 함께 와도 됨
def test_session_search_blank_values(client, repo, session_id):
    blank_session = _search(client, sessionId="")
    blank_region = _search(client, sessionId=str(session_id), region="")

    assert blank_session.status_code == 200
    assert "cache-control" not in blank_session.headers
    assert blank_region.status_code == 200
    assert repo.calls[0][0] == PolicyFilter()
    assert repo.calls[1][0].region_codes == ("11680",)


def test_session_search_rejects_region(client, repo, session_id):
    response = _search(client, sessionId=str(session_id), region="11680")

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert [detail["field"] for detail in body["details"]] == ["region"]
    assert repo.calls == []


# 다른 사용자의 세션·없는 세션·UUID가 아닌 값은 세션 API처럼 모두 없는 세션
@pytest.mark.parametrize("kind", ["other_user", "unknown", "not_uuid"])
def test_session_search_not_found(client, repo, session_id, kind):
    value, headers = {
        "other_user": (str(session_id), {"X-Client-Id": OTHER_CLIENT_ID}),
        "unknown": (str(uuid.uuid4()), HEADERS),
        "not_uuid": ("room-1", HEADERS),
    }[kind]

    response = client.get("/api/policies", params={"sessionId": value}, headers=headers)

    assert response.status_code == 404
    assert response.json()["code"] == "SESSION_NOT_FOUND"
    assert repo.calls == []


def test_policy_detail(client):
    response = client.get("/api/policies/P-1", headers=HEADERS)

    assert response.status_code == 200
    assert "cache-control" not in response.headers
    body = response.json()
    # 카드 필드는 검색 결과와 같음
    assert body["id"] == "P-1"
    assert body["regionLabel"] == "서울특별시"
    assert body["sourceUrl"] == "https://www.example.com"
    # 카드의 담당 기관은 주관 기관 그대로 (B7)
    assert body["organization"] == "서울특별시"
    assert {key: body[key] for key in ("summary", "eligibility", "benefits", "howToApply", "requiredDocuments")} == {
        "summary": "월세를 지원합니다.",
        "eligibility": "- 나이: 만 19~39세\n- 소득: 무관",
        "benefits": "월 20만 원",
        "howToApply": "온라인 신청",
        # 공백만 있는 원문은 비어 있는 것으로 봄
        "requiredDocuments": "공고에 나와 있지 않아요. 담당 기관(서울특별시, 운영: 서울청년센터)에 문의하거나 관련 사이트를 참고해 주세요.",
    }


def test_policy_detail_not_found(client, repo):
    response = client.get("/api/policies/P-404", headers=HEADERS)

    assert response.status_code == 404
    assert response.json() == {"code": "POLICY_NOT_FOUND", "message": "정책을 찾을 수 없습니다."}


# DB 컬럼보다 긴 ID는 조회하지 않고 같은 404
def test_policy_detail_long_id(client, repo):
    response = client.get("/api/policies/" + "1" * 31, headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["code"] == "POLICY_NOT_FOUND"
    assert repo.detail_calls == []


def test_policy_detail_requires_client_id(client):
    response = client.get("/api/policies/P-1")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CLIENT_ID"


# 원천에서 사라진 정책은 예전 채팅 카드에서 들어와도 없는 정책과 같게
def test_policy_detail_inactive_is_not_found(client):
    response = client.get("/api/policies/P-GONE", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["code"] == "POLICY_NOT_FOUND"
