import pytest

from app.pipeline.analyzer import AnalysisResult, FallbackReason, ResolvedSubQuery
from app.pipeline.conditions import Mismatch, RegionIndex, build_conditions
from app.repositories.policy_repository import PolicyFilter, RegionRow
from app.schemas.analysis import Intent, JobStatus, SchoolLevel, StatusFilter
from app.schemas.pipeline import Profile

REGIONS = RegionIndex([
    RegionRow("11", "서울특별시", "강남구", "11680"),
    RegionRow("11", "서울특별시", "서초구", "11650"),
    RegionRow("11", "서울특별시", "중구", "11140"),
    RegionRow("28", "인천광역시", "부평구", "28237"),
    RegionRow("28", "인천광역시", "중구", "28110"),
    RegionRow("43", "충청북도", "청주시 상당구", "43111"),
    RegionRow("43", "충청북도", "청주시 서원구", "43112"),
    RegionRow("12", "전남광주통합특별시", "북구", "12170"),
    RegionRow("41", "경기도", "광주시", "41610"),
    RegionRow("41", "경기도", "수원시 권선구", "41113"),
    RegionRow("41", "경기도", "수원시 영통구", "41117"),
    RegionRow("41", "경기도", "평택시", "41220"),
    RegionRow("50", "제주특별자치도", "제주시", "50110"),
    RegionRow("36", "세종특별자치시", "세종특별자치시", "36110"),
])
PROFILE = Profile(
    age=27, region_code="11680", job_code="0013003", school_code="0049005",
    marriage_status_code="0055002", annual_income=30_000_000,
)
CODE_NAMES = {"0013003": "미취업자", "0049005": "대학 재학", "0055002": "미혼"}
PROFILE_FILTER = PolicyFilter(
    sido_code="11", region_codes=("11680",), age=27, eligibility_codes={"JOB": "0013003", "SCHOOL": "0049005"},
    marriage_code="0055002", annual_income_won=30_000_000,
)


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
    ("text", "label"),
    [
        ("서울", "서울특별시"), ("서울시", "서울특별시"), ("서울특별시", "서울특별시"), ("충북", "충청북도"),
        ("강남구", "서울특별시 강남구"), ("강남", "서울특별시 강남구"), ("인천 부평구", "인천광역시 부평구"),
        # 구가 있는 시는 시 이름이나 구 이름 모두 그 시로 봄
        ("수원", "경기도 수원시"), ("수원시 영통구", "경기도 수원시"), ("영통구", "경기도 수원시"),
        ("청주시", "충청북도 청주시"), ("평택", "경기도 평택시"),
        ("광주", "전남광주통합특별시"), ("전남", "전남광주통합특별시"), ("광주광역시", "전남광주통합특별시"),
        # 정식 시군구 이름이 "○○시" 형태의 시도 줄임말보다 앞섬
        ("제주시", "제주특별자치도 제주시"), ("제주", "제주특별자치도"), ("세종", "세종특별자치시"),
        # 여러 시도에 있는 구 이름은 시도와 함께 말해야 알 수 있고, 목록에 없는 지명은 알 수 없음
        ("중구", None), ("서울 중구", "서울특별시 중구"), ("인천 중구", "인천광역시 중구"), ("판교", None),
        # 광주시는 경기도 광주시와 광주광역시 중 어느 쪽인지 알 수 없고, 시도와 함께 말하면 알 수 있음
        ("광주시", None), ("경기 광주시", "경기도 광주시"),
    ],
)
def test_region_index_resolve(text, label):
    region = REGIONS.resolve(text)

    assert (region.label if region else None) == label


def test_shared_short_name_is_unresolved():
    # 가상의 이름: 정식 이름은 다르지만 줄임말("한빛")이 같으면 어느 지역인지 알 수 없음
    regions = RegionIndex([
        RegionRow("41", "경기도", "한빛시", "41901"), RegionRow("43", "충청북도", "한빛군", "43901"),
    ])

    assert regions.resolve("한빛") is None
    assert regions.resolve("한빛시").label == "경기도 한빛시"
    assert regions.resolve("충북 한빛").label == "충청북도 한빛군"


@pytest.mark.parametrize(
    ("code", "label", "codes"),
    [
        ("11680", "서울특별시 강남구", ("11680",)),
        # 구가 있는 시는 그 시의 구 전체로 거름. region 표에 없는 시 코드도 같은 시로 봄
        ("41117", "경기도 수원시", ("41113", "41117")),
        ("41110", "경기도 수원시", ("41113", "41117")),
        # 시도만 오면 시도 전체, 모르는 시군구 코드는 잘못 빼지 않도록 시도 전체
        ("41000", "경기도", ()), ("41999", "경기도", ()),
        ("36110", "세종특별자치시", ("36110",)),
    ],
)
def test_profile_region(code, label, codes):
    region = REGIONS.profile_region(code)

    assert (region.label, region.codes) == (label, codes)


def test_profile_region_unknown_sido():
    assert REGIONS.profile_region("99000") is None
    assert REGIONS.profile_region(None) is None


def test_profile_conditions_used_when_question_has_none():
    conditions = build_conditions(_analysis(_sub()), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.to_filter() == PROFILE_FILTER
    assert conditions.lines == [
        "지역: 서울특별시 강남구", "나이: 27세", "취업 상태: 미취업자",
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
        **{**PROFILE_FILTER.__dict__, "exclude_closed": True, "exclude_upcoming": True}
    )
    assert conditions.mismatches == [
        Mismatch("지역", "인천 부평구", "서울특별시 강남구"), Mismatch("나이", "35세", "27세"),
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
    ("region_code", "question", "mismatch"),
    [
        # 필터 범위(프로필 시군구) 밖의 같은 시도 시군구도 다른 지역
        ("11680", "서초구", True), ("11680", "서울", False), ("11680", "강남", False),
        ("41117", "평택", True), ("41117", "권선구", False), ("41117", "수원", False), ("41117", "경기도", False),
        # 시도 전체 프로필은 그 시도의 시군구가 모두 범위 안
        ("41000", "평택", False), ("41000", "인천", True),
    ],
)
def test_region_mismatch_compares_with_filter_scope(region_code, question, mismatch):
    profile = Profile(region_code=region_code)

    conditions = build_conditions(_analysis(_sub(region=question)), profile, REGIONS, {})

    assert bool(conditions.ask_back) is mismatch


@pytest.mark.parametrize(
    "sub",
    [
        _sub(region="중구"), _sub(age=27), _sub(job_status=JobStatus.UNEMPLOYED),
        _sub(school=SchoolLevel.COLLEGE_ENROLLED),
    ],
)
def test_same_or_unresolved_condition_is_not_mismatch(sub):
    conditions = build_conditions(_analysis(sub), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.mismatches == []
    assert conditions.to_filter() == PROFILE_FILTER


def test_unknown_question_region_is_noted_for_answer():
    conditions = build_conditions(_analysis(_sub(region="판교")), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.mismatches == []
    assert conditions.unknown_region == "판교"
    assert conditions.lines[-1] == "질문에서 말한 지역: 판교 (어느 지역인지 알 수 없어 프로필 지역 기준으로 찾음)"


def test_mismatch_in_any_sub_query():
    conditions = build_conditions(_analysis(_sub(), _sub(region="인천")), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.ask_back == [Mismatch("지역", "인천", "서울특별시 강남구")]


def test_profile_conditions_used_when_analysis_failed():
    conditions = build_conditions(_analysis(failed=True), PROFILE, REGIONS, CODE_NAMES)

    assert conditions.to_filter() == PROFILE_FILTER
    assert conditions.mismatches == []
