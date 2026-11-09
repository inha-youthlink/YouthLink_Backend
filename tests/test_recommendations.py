import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.sessions import get_assist_service
from app.core.config import get_settings
from app.main import app
from app.repositories.policy_repository import PolicyFilter, PolicyRecord
from app.schemas.analysis import Category
from app.schemas.pipeline import Profile
from app.services.assist_service import AssistService, conversation_category
from tests.conftest import (
    CLIENT_ID,
    HEADERS,
    OTHER_CLIENT_ID,
    FakeChatRepository,
    FakeCodes,
    FakePolicyRepository,
)

PROFILE = Profile(
    age=27, region_code="11680", marriage_status_code="0055002", annual_income=30_000_000,
    major_code="0011005", job_code="0013003", school_code="0049007", special_target_code="0014010",
)
RECORD = PolicyRecord(
    policy_no="P-1", policy_name="청년 월세 지원", application_period_type_code="0057002",
    application_start_date=None, application_end_date=None, application_url=None,
    reference_url_1=None, reference_url_2=None, supervising_org_name="서울특별시", large_categories=["주거"],
)


@pytest.mark.parametrize(("recent", "expected"), [
    ([], None),
    ([["없음"], []], None),
    # 가장 많이 나온 분야
    ([["주거"], ["일자리"], ["주거", "없음"]], Category.HOUSING),
    # 동률이면 더 최근 (목록 앞이 최근)
    ([["일자리"], ["주거"]], Category.JOB),
    ([["교육", "주거"], ["주거", "교육"]], Category.EDUCATION),
    # 모르는 값은 세지 않음
    ([["금융"], ["참여"]], Category.PARTICIPATION),
])
def test_conversation_category(recent, expected):
    assert conversation_category(recent) == expected


class SearchPolicyRepository(FakePolicyRepository):
    def __init__(self) -> None:
        self.calls: list[tuple[PolicyFilter, int, int]] = []

    async def search(self, policy_filter, today, offset, limit, *, available_first=False):
        self.calls.append((policy_filter, offset, limit))
        self.available_first = available_first
        return [RECORD], 1

    async def get_region_labels(self, policy_nos):
        return {"P-1": "서울특별시"}


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def policies() -> SearchPolicyRepository:
    return SearchPolicyRepository()


@pytest.fixture
def codes() -> FakeCodes:
    return FakeCodes({})


@pytest.fixture
def client(repo, policies, codes):
    deps = SimpleNamespace(policy_repository=policies, common_code_repository=codes, settings=get_settings())
    app.dependency_overrides[get_assist_service] = lambda: AssistService(repo, deps)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def room_id(repo) -> uuid.UUID:
    return asyncio.run(repo.create_room(CLIENT_ID, PROFILE))


def _get(client, room_id, headers=HEADERS):
    return client.get(f"/api/sessions/{room_id}/recommendations", headers=headers)


def test_recommend_by_profile_and_category(client, repo, policies, room_id):
    repo.recent_categories[room_id] = [["주거"], ["일자리"], ["주거"]]

    response = _get(client, room_id)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    # 카드는 채팅·검색과 같은 형식
    assert [(p["id"], p["category"], p["regionLabel"], p["status"]) for p in response.json()] == [
        ("P-1", "HOUSING", "서울특별시", "ALWAYS_OPEN"),
    ]
    policy_filter, offset, limit = policies.calls[0]
    assert (offset, limit) == (0, 10)
    # 계약 "신청 가능한 정책 우선" (상시가 예정보다 앞)
    assert policies.available_first is True
    # 프로필 1차 필터 (채팅 검색과 같은 조건) + 마감 제외 + 대화 분야
    assert policy_filter.region_codes == ("11680",)
    assert policy_filter.age == 27
    assert policy_filter.eligibility_codes == {
        "JOB": "0013003", "SCHOOL": "0049007", "MAJOR": "0011005", "SPECIAL": "0014010",
    }
    assert policy_filter.marriage_code == "0055002"
    assert policy_filter.annual_income_won == 30_000_000
    assert policy_filter.exclude_closed is True
    assert policy_filter.exclude_upcoming is False
    assert policy_filter.large_categories == ("주거",)


# 대화 전이거나 분야가 없는 질문만 했으면 프로필 기준만
def test_recommend_without_category(client, repo, policies, room_id):
    repo.recent_categories[room_id] = [["없음"]]

    assert _get(client, room_id).status_code == 200
    assert policies.calls[0][0].large_categories == ()


# 최근 답변을 대화 기록 턴 수(기본 3)만큼만 봄 (그 이전은 지난 주제)
def test_recommend_uses_recent_three_answers(client, repo, policies, room_id):
    repo.recent_categories[room_id] = [["교육"], ["없음"], ["없음"], ["주거"], ["주거"]]

    _get(client, room_id)

    # 최근 3개(교육, 없음, 없음)에서 교육. 오래된 주거 두 번은 세지 않음
    assert policies.calls[0][0].large_categories == ("교육", "교육･직업훈련")


# 프로필 "기타" 코드는 1차 필터에서 거르지 않음 (채팅과 같음)
def test_recommend_other_code_not_filtered(client, codes, policies, room_id):
    codes.names["0013003"] = "기타"

    _get(client, room_id)

    assert "JOB" not in policies.calls[0][0].eligibility_codes


def test_recommend_other_users_session_is_not_found(client, policies, room_id):
    response = _get(client, room_id, headers={"X-Client-Id": OTHER_CLIENT_ID})

    assert response.status_code == 404
    assert response.json()["code"] == "SESSION_NOT_FOUND"
    assert policies.calls == []


def test_recommend_unknown_session(client):
    assert _get(client, uuid.uuid4()).status_code == 404
