from dataclasses import dataclass, field
from typing import Any

from app.pipeline.analyzer import AnalysisResult
from app.pipeline.context import PipelineContext
from app.repositories.policy_repository import PolicyFilter, RegionRow
from app.schemas.analysis import JobStatus, SchoolLevel, StatusFilter
from app.schemas.pipeline import Profile

REGION = "지역"
AGE = "나이"
JOB = "취업 상태"
SCHOOL = "학력"
# 다른 지역·나이로 거른 결과는 프로필 결과와 전혀 달라 되묻고, 취업 상태·학력은 가정 질문이 많아 답변에서 짚음
ASK_BACK_NAMES = frozenset({REGION, AGE})

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
}
# 경기도 광주시와 광주광역시를 함께 가리켜 시도를 정할 수 없음
AMBIGUOUS_NAMES = ("광주시",)
SIDO_SUFFIXES = ("특별자치시", "특별자치도", "통합특별시", "특별시", "광역시")


@dataclass(frozen=True)
class Condition:
    code: str | int
    label: str


# 질문에서 말한 조건이 프로필과 다른 경우. 거르는 데는 쓰지 않음
@dataclass(frozen=True)
class Mismatch:
    name: str
    question: str
    profile: str

    @property
    def asks_back(self) -> bool:
        return self.name in ASK_BACK_NAMES


@dataclass(frozen=True)
class SearchConditions:
    region: Condition | None = None
    age: Condition | None = None
    job: Condition | None = None
    school: Condition | None = None
    major: Condition | None = None
    special: Condition | None = None
    marriage: Condition | None = None
    income: Condition | None = None
    open_only: bool = False
    mismatches: list[Mismatch] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def ask_back(self) -> list[Mismatch]:
        return [mismatch for mismatch in self.mismatches if mismatch.asks_back]

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

    def to_trace(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "mismatches": [
                {"name": m.name, "question": m.question, "profile": m.profile, "asks_back": m.asks_back}
                for m in self.mismatches
            ],
        }


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
        for name in AMBIGUOUS_NAMES:
            self._by_name.pop(name, None)

    # 앞쪽 단어부터 시도를 찾아 "인천 부평구"는 인천, "강남구"는 서울로 봄
    def resolve(self, text: str) -> str | None:
        for token in text.replace(",", " ").split():
            code = self._by_name.get(token)
            if code:
                return code
        return None


# 1차 필터와 답변 생성의 프로필 표시가 함께 써서 요청마다 한 번만 조회함
async def load_regions(ctx: PipelineContext) -> RegionIndex:
    if ctx.regions is None:
        ctx.regions = RegionIndex(await ctx.deps.policy_repository.list_regions())
    return ctx.regions


async def load_code_names(ctx: PipelineContext) -> dict[str, str]:
    if ctx.code_names is None:
        profile = ctx.input.profile
        codes = [
            code for code in (
                profile.job_code, profile.school_code, profile.major_code,
                profile.special_target_code, profile.marriage_status_code,
            ) if code
        ]
        ctx.code_names = await ctx.deps.common_code_repository.get_names(codes)
    return ctx.code_names


def build_conditions(
    analysis: AnalysisResult | None,
    profile: Profile,
    regions: RegionIndex,
    code_names: dict[str, str],
) -> SearchConditions:
    # 질문 조건 추출이 틀려도 맞는 정책이 빠지지 않도록 거르는 조건은 프로필 값만 씀
    region_code = profile.region_code[:2] if profile.region_code else None
    region = (
        Condition(region_code, regions.sido_names[region_code]) if region_code in regions.sido_names else None
    )
    subs = [] if analysis is None or analysis.fallback_reason is not None else analysis.sub_queries
    conditions = SearchConditions(
        region=region,
        age=Condition(profile.age, f"{profile.age}세") if profile.age is not None else None,
        job=_profile_code(profile.job_code, code_names),
        school=_profile_code(profile.school_code, code_names),
        major=_profile_code(profile.major_code, code_names),
        special=_profile_code(profile.special_target_code, code_names),
        marriage=_profile_code(profile.marriage_status_code, code_names),
        income=(
            Condition(profile.annual_income, f"{profile.annual_income:,}원")
            if profile.annual_income is not None else None
        ),
        open_only=any(sub.status_filter == StatusFilter.OPEN_ONLY for sub in subs),
    )
    mismatches = _mismatches(analysis, conditions, regions) if subs else []
    return SearchConditions(**{
        **conditions.__dict__, "mismatches": mismatches, "lines": _lines(conditions, mismatches),
    })


# 복합 질문은 하위 질문 하나라도 다르면 그 하위 질문의 결과가 틀리므로 모든 하위 질문을 봄
def _mismatches(analysis: AnalysisResult, conditions: SearchConditions, regions: RegionIndex) -> list[Mismatch]:
    found: dict[str, Mismatch] = {}
    for sub in analysis.sub_queries:
        candidates = [
            # 시도로 바꿀 수 없는 지역("판교")은 다르다고 단정할 수 없어 프로필로 거름
            (REGION, sub.region, conditions.region, regions.resolve(sub.region) if sub.region else None),
            (AGE, f"{sub.age}세" if sub.age is not None else "", conditions.age, sub.age),
            (JOB, sub.job_status.value, conditions.job, JOB_CODES.get(sub.job_status)),
            (SCHOOL, sub.school.value, conditions.school, SCHOOL_CODES.get(sub.school)),
        ]
        for name, question, profile, code in candidates:
            if code is not None and profile is not None and str(code) != str(profile.code):
                found.setdefault(name, Mismatch(name, question, profile.label))
    return [found[name] for name in (REGION, AGE, JOB, SCHOOL) if name in found]


def _profile_code(code: str | None, code_names: dict[str, str]) -> Condition | None:
    if not code:
        return None
    return Condition(code, code_names.get(code, code))


def _lines(conditions: SearchConditions, mismatches: list[Mismatch]) -> list[str]:
    labeled = [
        (REGION, conditions.region),
        (AGE, conditions.age),
        (JOB, conditions.job),
        (SCHOOL, conditions.school),
        ("전공", conditions.major),
        ("특화 대상", conditions.special),
        ("혼인 상태", conditions.marriage),
        ("연소득", conditions.income),
    ]
    lines = [f"{name}: {condition.label}" for name, condition in labeled if condition]
    if conditions.open_only:
        lines.append("신청 상태: 지금 신청 가능한 정책만")
    lines += [
        f"질문에서 말한 {mismatch.name}: {mismatch.question} (프로필과 달라 프로필 기준으로 찾음)"
        for mismatch in mismatches
    ]
    return lines


def _short_sido_name(name: str) -> str:
    for suffix in SIDO_SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    # "충청북도" → "충북"처럼 네 글자 도는 첫 글자와 셋째 글자를 씀
    if name.endswith("도"):
        return name[0] + name[2] if len(name) == 4 else name.removesuffix("도")
    return name
