from dataclasses import dataclass, field

from app.pipeline.analyzer import AnalysisResult
from app.repositories.policy_repository import PolicyFilter, RegionRow
from app.schemas.analysis import JobStatus, SchoolLevel, StatusFilter
from app.schemas.pipeline import Profile

QUESTION = "질문"
PROFILE = "프로필"

JOB_CODES = {
    JobStatus.EMPLOYED: "0013001",
    JobStatus.SELF_EMPLOYED: "0013002",
    JobStatus.UNEMPLOYED: "0013003",
    JobStatus.FREELANCER: "0013004",
    JobStatus.DAILY_WORKER: "0013005",
    JobStatus.STARTUP: "0013006",
    JobStatus.SHORT_TERM_WORKER: "0013007",
    JobStatus.FARMER: "0013008",
}
SCHOOL_CODES = {
    SchoolLevel.BELOW_HIGH_SCHOOL: "0049001",
    SchoolLevel.HIGH_SCHOOL_ENROLLED: "0049002",
    SchoolLevel.HIGH_SCHOOL_EXPECTED: "0049003",
    SchoolLevel.HIGH_SCHOOL_GRADUATED: "0049004",
    SchoolLevel.COLLEGE_ENROLLED: "0049005",
    SchoolLevel.COLLEGE_EXPECTED: "0049006",
    SchoolLevel.COLLEGE_GRADUATED: "0049007",
    SchoolLevel.GRADUATE_SCHOOL: "0049008",
}

# 시도 이름의 줄임말 중 규칙으로 만들 수 없는 것 (옛 이름, 통합 전 이름)
SIDO_ALIASES = {
    "전라북도": "전북",
    "강원도": "강원",
    "제주도": "제주",
    "전라남도": "전남광주",
    "전남": "전남광주",
    "광주": "전남광주",
    "광주광역시": "전남광주",
    "광주시": "전남광주",
}
SIDO_SUFFIXES = ("특별자치시", "특별자치도", "통합특별시", "특별시", "광역시")


@dataclass(frozen=True)
class Condition:
    code: str | int
    label: str
    source: str


@dataclass(frozen=True)
class SearchConditions:
    region: Condition | None = None
    age: Condition | None = None
    job: Condition | None = None
    school: Condition | None = None
    # 질문에서 뽑지 않는 조건은 프로필 값만 씀
    major: Condition | None = None
    special: Condition | None = None
    marriage: Condition | None = None
    income: Condition | None = None
    open_only: bool = False
    # 질문에 지역이 있었지만 시도로 바꿀 수 없어 지역으로 거르지 않은 경우
    unresolved_region: str = ""
    lines: list[str] = field(default_factory=list)

    def to_filter(self) -> PolicyFilter:
        codes = {
            condition_type: str(condition.code)
            for condition_type, condition in (
                ("JOB", self.job), ("SCHOOL", self.school), ("MAJOR", self.major), ("SPECIAL", self.special)
            )
            if condition is not None
        }
        return PolicyFilter(
            sido_code=str(self.region.code) if self.region else None,
            age=int(self.age.code) if self.age else None,
            eligibility_codes=codes,
            marriage_code=str(self.marriage.code) if self.marriage else None,
            annual_income_won=int(self.income.code) if self.income else None,
            exclude_closed=self.open_only,
            exclude_upcoming=self.open_only,
        )


class RegionIndex:
    def __init__(self, rows: list[RegionRow]) -> None:
        self.sido_names = {row.sido_code: row.sido_name for row in rows}
        self._by_name: dict[str, str] = {}
        for code, name in self.sido_names.items():
            short = _short_sido_name(name)
            for key in (name, short, f"{short}시"):
                self._by_name[key] = code
        for alias, short in SIDO_ALIASES.items():
            code = self._by_name.get(short)
            if code:
                self._by_name.setdefault(alias, code)
        # "중구"처럼 여러 시도에 있는 시군구 이름은 어느 시도인지 알 수 없어 쓰지 않음
        sido_by_sigungu: dict[str, set[str]] = {}
        for row in rows:
            sido_by_sigungu.setdefault(row.sigungu_name, set()).add(row.sido_code)
        for sigungu, codes in sido_by_sigungu.items():
            if len(codes) == 1:
                self._by_name.setdefault(sigungu, next(iter(codes)))

    # 앞쪽 단어부터 시도를 찾아 "인천 부평구"는 인천, "강남구"는 서울로 봄
    def resolve(self, text: str) -> str | None:
        for token in text.replace(",", " ").split():
            code = self._by_name.get(token)
            if code:
                return code
        return None


def build_conditions(
    analysis: AnalysisResult | None,
    profile: Profile,
    regions: RegionIndex,
    code_names: dict[str, str],
) -> SearchConditions:
    # 분석이 실패하면 질문 조건을 알 수 없어, 질문과 다른 프로필 조건으로 잘못 거르지 않도록 거르지 않음
    if analysis is None or analysis.fallback_reason is not None:
        return SearchConditions()

    subs = analysis.sub_queries
    question_region = next((sub.region for sub in subs if sub.region), "")
    question_age = next((sub.age for sub in subs if sub.age is not None), None)
    question_job = next((sub.job_status for sub in subs if sub.job_status != JobStatus.NONE), None)
    question_school = next((sub.school for sub in subs if sub.school != SchoolLevel.NONE), None)

    region, unresolved = _region(question_region, profile, regions)
    conditions = SearchConditions(
        region=region,
        age=(
            Condition(question_age, f"{question_age}세", QUESTION) if question_age is not None
            else Condition(profile.age, f"{profile.age}세", PROFILE) if profile.age is not None
            else None
        ),
        job=(
            Condition(JOB_CODES[question_job], question_job.value, QUESTION) if question_job
            else _profile_code(profile.job_code, code_names)
        ),
        school=(
            Condition(SCHOOL_CODES[question_school], question_school.value, QUESTION) if question_school
            else _profile_code(profile.school_code, code_names)
        ),
        major=_profile_code(profile.major_code, code_names),
        special=_profile_code(profile.special_target_code, code_names),
        marriage=_profile_code(profile.marriage_status_code, code_names),
        income=(
            Condition(profile.annual_income, f"{profile.annual_income:,}원", PROFILE)
            if profile.annual_income is not None else None
        ),
        open_only=any(sub.status_filter == StatusFilter.OPEN_ONLY for sub in subs),
        unresolved_region=unresolved,
    )
    return SearchConditions(**{**conditions.__dict__, "lines": _lines(conditions)})


def _region(question_region: str, profile: Profile, regions: RegionIndex) -> tuple[Condition | None, str]:
    if question_region:
        code = regions.resolve(question_region)
        # 질문에 지역이 있는데 알 수 없으면 프로필 지역으로 거르면 질문과 다른 지역만 남아 거르지 않음
        if code is None:
            return None, question_region
        return Condition(code, regions.sido_names[code], QUESTION), ""
    if profile.region_code:
        code = profile.region_code[:2]
        if code in regions.sido_names:
            return Condition(code, regions.sido_names[code], PROFILE), ""
    return None, ""


def _profile_code(code: str | None, code_names: dict[str, str]) -> Condition | None:
    if not code:
        return None
    return Condition(code, code_names.get(code, code), PROFILE)


def _lines(conditions: SearchConditions) -> list[str]:
    labeled = [
        ("지역", conditions.region),
        ("나이", conditions.age),
        ("취업 상태", conditions.job),
        ("학력", conditions.school),
        ("전공", conditions.major),
        ("특화 대상", conditions.special),
        ("혼인 상태", conditions.marriage),
        ("연소득", conditions.income),
    ]
    lines = [f"{name}: {condition.label} ({condition.source})" for name, condition in labeled if condition]
    if conditions.unresolved_region:
        lines.append(f"지역: {conditions.unresolved_region} (질문, 지역 코드로 바꿀 수 없어 거르지 않음)")
    if conditions.open_only:
        lines.append("신청 상태: 지금 신청 가능한 정책만 (질문)")
    return lines


def _short_sido_name(name: str) -> str:
    for suffix in SIDO_SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    # "충청북도" → "충북"처럼 네 글자 도는 첫 글자와 셋째 글자를 씀
    if name.endswith("도"):
        return name[0] + name[2] if len(name) == 4 else name.removesuffix("도")
    return name
