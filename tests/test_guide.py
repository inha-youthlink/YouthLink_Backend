import asyncio
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, OpenAIError

from app.api.sessions import get_guide_service
from app.core.config import get_settings
from app.core.dependencies import get_guide_store, get_rate_limits
from app.core.rate_limit import RateLimits
from app.llm.client import ParsedResult
from app.main import app
from app.pipeline.conditions import RegionIndex, build_conditions
from app.repositories.policy_repository import PolicyDetailRecord, PolicyRecord
from app.schemas.guide import GuideDraft, GuideStep, GuideTerm, ObservedFact
from app.schemas.pipeline import ConversationTurn, Profile
from app.schemas.policy_info import KST
from app.services import guide_service
from app.services.guide_service import GuideService, conversation_lines, guide_content
from app.services.guide_store import GuideStore
from tests.conftest import CLIENT_ID, HEADERS, OTHER_CLIENT_ID, REGIONS, FakeChatRepository, FakeCodes, FakePolicyRepository

PROFILE = Profile(age=27, region_code="11680", job_code="0013003")
POLICY_NO = "P-GUIDE"
NOW = datetime(2026, 11, 14, 20, 30, tzinfo=KST)


def _detail(**overrides) -> PolicyDetailRecord:
    record = PolicyRecord(
        policy_no=POLICY_NO,
        policy_name="청년 월세 지원",
        application_period_type_code=None,
        application_start_date=date(2026, 10, 1),
        application_end_date=date(2026, 11, 30),
        application_url="www.apply.go.kr",
        reference_url_1="https://www.apply.go.kr",
        reference_url_2="https://www.info.go.kr",
        supervising_org_name="서울특별시",
    )
    values = dict(
        record=record,
        description="요약",
        support_content="월 20만 원 지원",
        application_method="복지로에서 온라인 신청",
        submission_documents="임대차계약서, 월세 이체 증빙",
        additional_qualification="무주택자",
        participation_exclusion="기존 수혜자",
        operating_org_name="서울청년센터",
        min_age=19,
        max_age=34,
        income_condition_code=None,
        min_income=None,
        max_income=None,
        income_etc=None,
        marriage_name=None,
        screening_method="서류 심사",
        additional_notes="예산 소진 시 마감",
        synced_at=datetime(2026, 11, 9, 18, tzinfo=UTC),
    )
    return PolicyDetailRecord(**{**values, **overrides})


DRAFT = GuideDraft(
    observed=[ObservedFact(item="주거 상황", value="월세 원룸 거주"), ObservedFact(item="빈 값", value=" ")],
    conditions=["무주택자", "참여 제한: 기존 수혜자"],
    steps=[GuideStep(action="복지로에서 온라인 신청", where="복지로"), GuideStep(action=" ", where="")],
    documents=["임대차계약서", "월세 이체 증빙"],
    cautions=["예산 소진 시 조기 마감"],
    terms=[GuideTerm(term="무주택자", meaning="본인 명의 주택이 없는 사람")],
)


class GuidePolicyRepository(FakePolicyRepository):
    def __init__(self, detail: PolicyDetailRecord | None) -> None:
        self.detail = detail
        self.in_profile = True
        self.filters = []

    async def matches_filter(self, policy_filter, policy_no, today):
        self.filters.append((policy_filter, policy_no))
        return self.in_profile

    async def get_detail(self, policy_no):
        return self.detail if self.detail and policy_no == self.detail.record.policy_no else None

    async def get_region_labels(self, policy_nos):
        return {policy_no: "서울특별시" for policy_no in policy_nos}


class FakeLLM:
    def __init__(self) -> None:
        self.parsed: GuideDraft | None = DRAFT
        self.error: BaseException | None = None
        self.calls: list[dict] = []

    async def structured(self, messages, text_format, *, timeout):
        self.calls.append({"messages": messages, "text_format": text_format, "timeout": timeout})
        if self.error:
            raise self.error
        return ParsedResult(parsed=self.parsed, prompt_tokens=10, completion_tokens=5)


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def policies() -> GuidePolicyRepository:
    return GuidePolicyRepository(_detail())


@pytest.fixture
def store(tmp_path) -> GuideStore:
    return GuideStore(tmp_path, 600)


@pytest.fixture
def client(repo, llm, policies, store):
    deps = SimpleNamespace(
        policy_repository=policies, common_code_repository=FakeCodes({"0013003": "미취업자"}), llm=llm,
        settings=get_settings(),
    )
    app.dependency_overrides[get_guide_service] = lambda: GuideService(repo, deps, store)
    app.dependency_overrides[get_guide_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def room_id(repo) -> uuid.UUID:
    return asyncio.run(repo.create_room(CLIENT_ID, PROFILE))


def _turn(repo, room_id, question, answer):
    async def save():
        question_id = await repo.save_question(room_id, uuid.uuid4(), question, question)
        await repo.save_answer(
            room_id=room_id, question_id=question_id, answer=answer, answer_metadata={},
            policies=[], current_policies=[],
        )
    asyncio.run(save())


def _create(client, room_id, policy_id=POLICY_NO, headers=HEADERS):
    return client.post(f"/api/sessions/{room_id}/guides", json={"policyId": policy_id}, headers=headers)


def test_create_guide(client, repo, llm, room_id):
    _turn(repo, room_id, "월세 원룸에 혼자 살아요", "청년 월세 지원이 있어요.")

    response = _create(client, room_id)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["policyId"] == POLICY_NO
    assert body["pdfUrl"].startswith("/api/guides/") and body["pdfUrl"].endswith(".pdf")
    assert body["expiresAt"].endswith("+09:00")
    assert body["outsideProfile"] is False
    # 받은 경로로 PDF가 열림 (X-Client-Id 없이)
    pdf = client.get(body["pdfUrl"])
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    call = llm.calls[0]
    assert call["text_format"] is GuideDraft
    assert call["timeout"] == get_settings().guide_timeout
    text = call["messages"][1]["content"]
    for expected in ["[정책] 청년 월세 지원", "## 신청 방법\n복지로에서 온라인 신청", "## 심사 방법\n서류 심사",
                     "지역: 서울특별시 강남구", "1. 질문: 월세 원룸에 혼자 살아요"]:
        assert expected in text


# 나눌 원문도 대화도 없으면 LLM 없이 "공고에 없음" 가이드를 만들고 하루 상한에 세지 않음
def test_create_guide_without_sources_and_conversation(client, llm, policies, room_id):
    empty = {name: None for name in (
        "support_content", "application_method", "submission_documents", "additional_qualification",
        "participation_exclusion", "screening_method", "additional_notes",
    )}
    policies.detail = _detail(**empty)
    limits = RateLimits(get_settings().model_copy(update={"llm_daily_limit": 1}))
    app.dependency_overrides[get_rate_limits] = lambda: limits

    response = _create(client, room_id)

    assert response.status_code == 201
    assert llm.calls == []
    assert limits.llm_daily.hit() is None


# 프로필 필터 밖 정책도 만들되 응답에 표시하고 주의사항 맨 앞에 경고 (채팅 추천과 같은 필터로 확인)
def test_create_guide_outside_profile_warns(client, policies, room_id, monkeypatch):
    policies.in_profile = False
    contents = []
    original = guide_service.guide_content

    def record(*args, **kwargs):
        contents.append(original(*args, **kwargs))
        return contents[-1]

    monkeypatch.setattr(guide_service, "guide_content", record)

    response = _create(client, room_id)

    assert response.status_code == 201
    assert response.json()["outsideProfile"] is True
    policy_filter, policy_no = policies.filters[0]
    assert (policy_filter.region_codes, policy_filter.age, policy_no) == (("11680",), 27, POLICY_NO)
    assert contents[0].cautions[0] == guide_service.OUTSIDE_PROFILE_CAUTION


def test_create_guide_counts_daily_llm_limit(client, room_id):
    limits = RateLimits(get_settings().model_copy(update={"llm_daily_limit": 1}))
    app.dependency_overrides[get_rate_limits] = lambda: limits

    assert _create(client, room_id).status_code == 201
    second = _create(client, room_id)
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"


@pytest.mark.parametrize(("policy_id", "status", "code"), [
    ("P-NONE", 404, "POLICY_NOT_FOUND"),
    ("P" * 31, 400, "VALIDATION_FAILED"),
    ("  ", 400, "VALIDATION_FAILED"),
])
def test_create_guide_bad_policy(client, llm, room_id, policy_id, status, code):
    response = _create(client, room_id, policy_id=policy_id)

    assert response.status_code == status
    assert response.json()["code"] == code
    assert llm.calls == []


def test_create_guide_inactive_policy(client, llm, policies, room_id):
    policies.detail = _detail(is_active=False)

    response = _create(client, room_id)

    assert response.status_code == 404
    assert response.json()["code"] == "POLICY_NOT_FOUND"


def test_create_guide_other_users_session(client, llm, room_id):
    response = _create(client, room_id, headers={"X-Client-Id": OTHER_CLIENT_ID})

    assert response.status_code == 404
    assert response.json()["code"] == "SESSION_NOT_FOUND"
    assert llm.calls == []


@pytest.mark.parametrize(("error", "status", "code"), [
    (APITimeoutError(request=httpx2.Request("POST", "https://llm.test")), 504, "RAG_TIMEOUT"),
    (OpenAIError("gateway down"), 502, "RAG_UPSTREAM_ERROR"),
])
def test_create_guide_llm_errors(client, llm, room_id, error, status, code):
    llm.error = error

    response = _create(client, room_id)

    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["message"].startswith("가이드를 만드")
    assert "gateway" not in body["message"]


def test_create_guide_refused_output(client, llm, room_id):
    llm.parsed = None

    response = _create(client, room_id)

    assert response.status_code == 502
    assert response.json()["code"] == "RAG_UPSTREAM_ERROR"


def _conditions():
    return build_conditions(None, PROFILE, RegionIndex(REGIONS), {"0013003": "미취업자"})


def test_guide_content():
    content = guide_content(_detail(first_come_first_served=True), _conditions(), DRAFT, "서울특별시", NOW)

    assert content.overview == [
        ("담당 기관", "서울특별시, 운영: 서울청년센터"),
        ("지역", "서울특별시"),
        ("신청 상태", "신청 가능 (2026-10-01 ~ 2026-11-30)"),
        ("신청 사이트", "https://www.apply.go.kr"),
        # 신청 사이트와 같은 주소는 건너뜀
        ("관련 사이트", "https://www.info.go.kr"),
    ]
    assert content.profile == [("지역", "서울특별시 강남구"), ("나이", "27세"), ("취업 상태", "미취업자")]
    # 빈 값은 뺌
    assert content.observed == [("주거 상황", "월세 원룸 거주")]
    assert content.steps == [("복지로에서 온라인 신청", "복지로")]
    # 기본 조건은 코드, 추가 자격·참여 제한은 LLM
    assert content.conditions == ["나이: 만 19~34세", "무주택자", "참여 제한: 기존 수혜자"]
    assert content.cautions == ["선착순으로 마감될 수 있어요.", "예산 소진 시 조기 마감"]
    assert guide_service.OUTSIDE_PROFILE_CAUTION not in content.cautions
    assert content.documents_raw == "임대차계약서, 월세 이체 증빙"
    assert content.synced_on == date(2026, 11, 10)
    assert content.source_url == "https://www.apply.go.kr"


# LLM이 추가 자격·참여 제한을 나누지 못하면 원문을 그대로 넣어 조건이 빠지지 않게 함
def test_guide_content_falls_back_to_raw_conditions():
    draft = DRAFT.model_copy(update={"conditions": []})

    content = guide_content(_detail(), _conditions(), draft, None, NOW)

    assert content.conditions[-2:] == ["추가 자격: 무주택자", "참여 제한: 기존 수혜자"]


@pytest.mark.parametrize(("end", "period_type", "expected"), [
    (date(2026, 11, 14), None, "신청 마감이 2026-11-14이에요 (오늘 마감)."),
    (date(2026, 11, 20), None, "신청 마감이 2026-11-20이에요 (6일 남음)."),
    (date(2026, 11, 13), None, "지금은 신청 기간이 아니에요. 다음 공고 일정을 담당 기관에 확인해 주세요."),
    (None, "0057002", None),
])
def test_guide_content_deadline_cautions(end, period_type, expected):
    detail = _detail()
    record = detail.record.__class__(**{
        **detail.record.__dict__, "application_end_date": end, "application_period_type_code": period_type,
    })
    draft = DRAFT.model_copy(update={"cautions": []})

    content = guide_content(_detail(record=record), _conditions(), draft, None, NOW)

    assert content.cautions == ([expected] if expected else [])


def test_conversation_lines_cut_answers_and_drop_oldest(monkeypatch):
    turns = [ConversationTurn(question=f"질문 {n}", answer="가" * 500) for n in range(3)]

    lines = conversation_lines(turns)

    assert lines[0] == "1. 질문: 질문 0"
    assert lines[1] == f"   답변 앞부분: {'가' * guide_service.GUIDE_ANSWER_CHARS}…(이하 생략)"

    # 상한을 넘으면 오래된 턴부터 뺌
    monkeypatch.setattr(guide_service, "GUIDE_CONVERSATION_MAX_CHARS", 700)
    assert [line for line in conversation_lines(turns) if "질문:" in line] == ["1. 질문: 질문 1", "2. 질문: 질문 2"]
