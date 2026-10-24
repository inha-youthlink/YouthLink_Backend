import pytest

from app.pipeline.analyzer import AnalysisResult, FallbackReason, ResolvedSubQuery
from app.pipeline.conditions import RegionIndex, build_conditions
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
        "지역: 서울특별시 (프로필)", "나이: 27세 (프로필)", "취업 상태: 미취업자 (프로필)",
        "학력: 대학 재학 (프로필)", "혼인 상태: 미혼 (프로필)", "연소득: 30,000,000원 (프로필)",
    ]


def test_question_conditions_override_profile():
    sub = _sub(
        region="인천 부평구", age=35, job_status=JobStatus.EMPLOYED, school=SchoolLevel.GRADUATE_SCHOOL,
        status_filter=StatusFilter.OPEN_ONLY,
    )

    conditions = build_conditions(_analysis(sub), PROFILE, REGIONS, CODE_NAMES)

    policy_filter = conditions.to_filter()
    assert (policy_filter.sido_code, policy_filter.age) == ("28", 35)
    assert policy_filter.eligibility_codes == {"JOB": "0013001", "SCHOOL": "0049008"}
    assert policy_filter.exclude_closed and policy_filter.exclude_upcoming
    assert conditions.lines[:4] == [
        "지역: 인천광역시 (질문)", "나이: 35세 (질문)", "취업 상태: 재직자 (질문)", "학력: 석·박사 (질문)",
    ]


def test_unresolved_question_region_does_not_fall_back_to_profile():
    conditions = build_conditions(_analysis(_sub(region="판교")), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.to_filter().sido_code is None
    assert "지역: 판교 (질문, 지역 코드로 바꿀 수 없어 거르지 않음)" in conditions.lines


def test_no_conditions_when_analysis_failed():
    conditions = build_conditions(_analysis(failed=True), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.to_filter().is_empty()
    assert conditions.lines == []
