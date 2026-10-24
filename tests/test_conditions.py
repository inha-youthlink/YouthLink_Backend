import pytest

from app.pipeline.analyzer import AnalysisResult, FallbackReason, ResolvedSubQuery
from app.pipeline.conditions import Mismatch, RegionIndex, build_conditions
from app.repositories.policy_repository import PolicyFilter, RegionRow
from app.schemas.analysis import Intent, JobStatus, SchoolLevel, StatusFilter
from app.schemas.pipeline import Profile

REGIONS = RegionIndex([
    RegionRow("11", "서울특별시", "강남구"),
    RegionRow("11", "서울특별시", "중구"),
    RegionRow("28", "인천광역시", "부평구"),
    RegionRow("28", "인천광역시", "중구"),
    RegionRow("43", "충청북도", "청주시"),
    RegionRow("12", "전남광주통합특별시", "북구"),
    RegionRow("41", "경기도", "광주시"),
])
PROFILE = Profile(
    age=27, region_code="11680", job_code="0013003", school_code="0049005",
    marriage_status_code="0055002", annual_income=30_000_000,
)
CODE_NAMES = {"0013003": "미취업자", "0049005": "대학 재학", "0055002": "미혼"}


def _analysis(*subs: ResolvedSubQuery, failed: bool = False) -> AnalysisResult:
    return AnalysisResult(
        sub_queries=list(subs), is_new_topic=False,
        fallback_reason=FallbackReason.LLM_ERROR if failed else None, prompt_version="analyze_v3", model="m",
    )


def _sub(**fields) -> ResolvedSubQuery:
    return ResolvedSubQuery(
        standalone_question="q", target_refs=[], targets=[], intent=Intent.POLICY_LIST,
        status_filter=fields.pop("status_filter", StatusFilter.NONE), **fields,
    )


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("서울", "11"), ("서울시", "11"), ("서울특별시", "11"), ("강남구", "11"),
        ("인천 부평구", "28"), ("충북", "43"), ("충청북도", "43"), ("광주", "12"), ("전남", "12"),
        # 여러 시도에 있는 구 이름은 시도를 정할 수 없음
        ("중구", None), ("판교", None),
        # 광주시는 경기도 광주시와 광주광역시 중 어느 쪽인지 알 수 없음
        ("광주시", None), ("경기 광주시", "41"), ("광주광역시", "12"),
    ],
)
def test_region_index_resolve(text, code):
    assert REGIONS.resolve(text) == code


def test_profile_conditions_used_when_question_has_none():
    conditions = build_conditions(_analysis(_sub()), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.to_filter() == PolicyFilter(
        sido_code="11", age=27, eligibility_codes={"JOB": "0013003", "SCHOOL": "0049005"},
        marriage_code="0055002", annual_income_won=30_000_000,
    )
    assert conditions.lines == [
        "지역: 서울특별시", "나이: 27세", "취업 상태: 미취업자",
        "학력: 대학 재학", "혼인 상태: 미혼", "연소득: 30,000,000원",
    ]
    assert conditions.mismatches == []


def test_question_conditions_detected_but_not_used_for_filter():
    sub = _sub(
        region="인천 부평구", age=35, job_status=JobStatus.EMPLOYED, school=SchoolLevel.GRADUATE_SCHOOL,
        status_filter=StatusFilter.OPEN_ONLY,
    )

    conditions = build_conditions(_analysis(sub), PROFILE, REGIONS, CODE_NAMES)

    # 신청 상태만 질문에서 가져옴
    assert conditions.to_filter() == PolicyFilter(
        sido_code="11", age=27, eligibility_codes={"JOB": "0013003", "SCHOOL": "0049005"},
        marriage_code="0055002", annual_income_won=30_000_000, exclude_closed=True, exclude_upcoming=True,
    )
    assert conditions.mismatches == [
        Mismatch("지역", "인천 부평구", "서울특별시"), Mismatch("나이", "35세", "27세"),
        Mismatch("취업 상태", "재직자", "미취업자"), Mismatch("학력", "석·박사", "대학 재학"),
    ]
    assert [mismatch.name for mismatch in conditions.ask_back] == ["지역", "나이"]
    assert conditions.lines[-5:] == [
        "신청 상태: 지금 신청 가능한 정책만",
        "질문에서 말한 지역: 인천 부평구 (프로필과 달라 프로필 기준으로 찾음)",
        "질문에서 말한 나이: 35세 (프로필과 달라 프로필 기준으로 찾음)",
        "질문에서 말한 취업 상태: 재직자 (프로필과 달라 프로필 기준으로 찾음)",
        "질문에서 말한 학력: 석·박사 (프로필과 달라 프로필 기준으로 찾음)",
    ]


@pytest.mark.parametrize(
    "sub",
    [
        # 같은 시도의 구, 시도로 바꿀 수 없는 지역, 여러 시도에 있는 구 이름
        _sub(region="강남구"), _sub(region="서울"), _sub(region="판교"), _sub(region="중구"),
        _sub(age=27), _sub(job_status=JobStatus.UNEMPLOYED), _sub(school=SchoolLevel.COLLEGE_ENROLLED),
    ],
)
def test_same_or_unresolved_condition_is_not_mismatch(sub):
    conditions = build_conditions(_analysis(sub), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.mismatches == []
    assert conditions.to_filter().sido_code == "11"


def test_mismatch_in_any_sub_query():
    conditions = build_conditions(_analysis(_sub(), _sub(region="인천")), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.ask_back == [Mismatch("지역", "인천", "서울특별시")]


def test_profile_conditions_used_when_analysis_failed():
    conditions = build_conditions(_analysis(failed=True), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.to_filter() == PolicyFilter(
        sido_code="11", age=27, eligibility_codes={"JOB": "0013003", "SCHOOL": "0049005"},
        marriage_code="0055002", annual_income_won=30_000_000,
    )
    assert conditions.mismatches == []
