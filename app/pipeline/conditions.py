from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.pipeline.analyzer import AnalysisResult, ResolvedSubQuery
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
OTHER_CODE_NAME = "기타"

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
SIGUNGU_SUFFIXES = ("시", "군", "구")

K = TypeVar("K")


@dataclass(frozen=True)
class Condition:
    code: str | int
    label: str


# 프로필과 질문의 지역. codes가 비면 시도 전체
@dataclass(frozen=True)
class Region:
    sido_code: str
    sido_name: str
    label: str
    codes: tuple[str, ...] = ()

    # 질문 지역이 프로필 지역의 필터 범위 안이면 같은 지역으로 봄 ("권선구"는 수원시 프로필의 범위 안)
    def covers(self, other: "Region") -> bool:
        if other.sido_code != self.sido_code:
            return False
        return not self.codes or not other.codes or bool(set(self.codes) & set(other.codes))


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
    region: Region | None = None
    age: Condition | None = None
    job: Condition | None = None
    school: Condition | None = None
    major: Condition | None = None
    special: Condition | None = None
    marriage: Condition | None = None
    income: Condition | None = None
    open_only: bool = False
    mismatches: list[Mismatch] = field(default_factory=list)
    # 질문에 지역이 있었지만 어느 지역인지 알 수 없어 프로필 지역으로 거른 경우
    unknown_region: str = ""
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
            sido_code=self.region.sido_code if self.region else None,
            region_codes=self.region.codes if self.region else (),
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
            "unknown_region": self.unknown_region,
            "mismatches": [
                {"name": m.name, "question": m.question, "profile": m.profile, "asks_back": m.asks_back}
                for m in self.mismatches
            ],
        }


class RegionIndex:
    def __init__(self, rows: list[RegionRow]) -> None:
        self.sido_names = {row.sido_code: row.sido_name for row in rows}
        self._by_code: dict[str, Region] = {}
        named: list[tuple[str, Region]] = []
        units = _sigungu_units(rows)
        # 지역 목록 API가 필터와 같은 단위로 보여 주려고 남김
        self.units = [unit for unit, _ in units]
        for unit, names in units:
            for code in unit.codes:
                self._by_code[code] = unit
            named += [(name, unit) for name in names]
        shortened = [(short, unit) for name, unit in named if (short := _short_sigungu_name(name))]

        sido_keys: dict[str, str] = {}
        for code, name in self.sido_names.items():
            sido_keys[name] = code
            sido_keys[_short_sido_name(name)] = code
        for alias, short in SIDO_ALIASES.items():
            if short in sido_keys:
                sido_keys.setdefault(alias, sido_keys[short])

        # 시도와 함께 말하면("대구 중구") 그 시도 안에서는 이름이 하나라 여러 시도에 있는 이름도 찾을 수 있음
        self._in_sido: dict[tuple[str, str], Region] = {
            **_only_unique(((unit.sido_code, short), unit) for short, unit in shortened),
            **_only_unique(((unit.sido_code, name), unit) for name, unit in named),
        }

        # "중구"처럼 여러 지역에 걸리는 이름은 이름만으로 어느 지역인지 알 수 없어 쓰지 않음
        full_names = _only_unique((name, unit) for name, unit in named if name not in AMBIGUOUS_NAMES)
        short_names = _only_unique(shortened)

        # 정식 이름이 줄임말보다 앞섬: "제주시"는 제주도가 아니라 제주시, "광주"는 광주시가 아니라 시도
        self._by_name: dict[str, Region] = {
            name: self.sido_region(code) for name, code in sido_keys.items()
        }
        for name, unit in full_names.items():
            self._by_name.setdefault(name, unit)
        for code, name in self.sido_names.items():
            self._by_name.setdefault(f"{_short_sido_name(name)}시", self.sido_region(code))
        for name, unit in short_names.items():
            self._by_name.setdefault(name, unit)

    def sido_region(self, sido_code: str) -> Region:
        name = self.sido_names[sido_code]
        return Region(sido_code, name, name)

    # 시도만 온 코드("41000")는 시도 전체, region 표에 없는 시 코드("41110")는 그 시의 구 전체로 봄
    def profile_region(self, code: str | None) -> Region | None:
        if not code or code[:2] not in self.sido_names:
            return None
        if code in self._by_code:
            return self._by_code[code]
        city = self._city(code)
        if city is not None:
            return city
        # 모르는 시군구 코드는 잘못 빼지 않도록 시도 전체로 거름
        return self.sido_region(code[:2])

    # 모르는 코드도 profile_region이 시도 전체로 받아 주므로, 세션을 만들 때 이것으로 먼저 막음
    def is_profile_code(self, code: str) -> bool:
        if code in self._by_code:
            return True
        if code[:2] in self.sido_names and code[2:] == "000":
            return True
        return self._city(code) is not None

    # 구가 있는 시는 구 코드 대신 시 코드("41110")를 씀. 1차 필터가 그 시 전체로 거르므로 구를 골라도 결과가 같음
    @staticmethod
    def unit_code(unit: Region) -> str:
        return unit.codes[0] if len(unit.codes) == 1 else unit.codes[0][:4] + "0"

    def _city(self, code: str) -> Region | None:
        if not code.endswith("0"):
            return None
        return next((unit for c, unit in self._by_code.items() if c[:4] == code[:4] and len(unit.codes) > 1), None)

    # "인천 부평구"처럼 시도와 시군구가 함께 있으면 더 좁은 시군구를 씀
    def resolve(self, text: str) -> Region | None:
        tokens = text.replace(",", " ").split()
        found = [self._by_name[token] for token in tokens if token in self._by_name]
        if not found:
            return None
        sido = next((region for region in found if not region.codes), None)
        if sido is not None:
            # 시도로 읽은 단어("제주")는 그 시도 안의 줄임말("제주시")로 다시 읽지 않음
            rest = [token for token in tokens if self._by_name.get(token) != sido]
            return next((self._in_sido[key] for token in rest if (key := (sido.sido_code, token)) in self._in_sido), sido)
        return found[0]


# 서울·광역시는 구, 도는 시·군 단위. 구가 있는 시(수원시 영통구)는 그 시의 구를 하나로 묶고 구 이름으로도 찾음
def _sigungu_units(rows: list[RegionRow]) -> list[tuple[Region, list[str]]]:
    groups: dict[tuple[str, str], list[RegionRow]] = {}
    for row in rows:
        city = row.sigungu_name.split()[0]
        groups.setdefault((row.sido_code, city), []).append(row)
    units = []
    for (sido_code, city), members in groups.items():
        sido_name = members[0].sido_name
        # 세종처럼 시군구 이름이 시도 이름과 같으면 시도 이름만 씀
        label = sido_name if city == sido_name else f"{sido_name} {city}"
        unit = Region(sido_code, sido_name, label, tuple(sorted(row.region_code for row in members)))
        gu_names = [row.sigungu_name.split()[1] for row in members if len(row.sigungu_name.split()) > 1]
        units.append((unit, ([] if city == sido_name else [city]) + gu_names))
    return units


def _only_unique(pairs: Iterable[tuple[K, Region]]) -> dict[K, Region]:
    found: dict[K, set[Region]] = {}
    for key, unit in pairs:
        found.setdefault(key, set()).add(unit)
    return {key: next(iter(units)) for key, units in found.items() if len(units) == 1}


def _short_sigungu_name(name: str) -> str:
    # "중구"처럼 줄이면 한 글자가 되는 이름은 다른 단어와 겹쳐 줄이지 않음
    for suffix in SIGUNGU_SUFFIXES:
        if name.endswith(suffix) and len(name) - len(suffix) >= 2:
            return name.removesuffix(suffix)
    return ""


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
    subs = [] if analysis is None or analysis.fallback_reason is not None else analysis.sub_queries
    conditions = SearchConditions(
        region=regions.profile_region(profile.region_code),
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
    mismatches = _mismatches(subs, conditions, regions)
    # 프로필 지역이 없으면 지역으로 거르지 않아 알릴 것이 없음
    unknown_region = "" if conditions.region is None else next(
        (sub.region for sub in subs if sub.region and regions.resolve(sub.region) is None), ""
    )
    conditions = SearchConditions(**{**conditions.__dict__, "mismatches": mismatches, "unknown_region": unknown_region})
    return SearchConditions(**{**conditions.__dict__, "lines": _lines(conditions)})


# 복합 질문은 하위 질문 하나라도 다르면 그 하위 질문의 결과가 틀리므로 모든 하위 질문을 봄
def _mismatches(subs: list[ResolvedSubQuery], conditions: SearchConditions, regions: RegionIndex) -> list[Mismatch]:
    found: dict[str, Mismatch] = {}
    for sub in subs:
        # 어느 지역인지 알 수 없는 지역("판교")은 다르다고 단정할 수 없어 프로필로 거름
        place = regions.resolve(sub.region) if sub.region else None
        if place is not None and conditions.region is not None and not conditions.region.covers(place):
            found.setdefault(REGION, Mismatch(REGION, sub.region, conditions.region.label))
        candidates = [
            (AGE, f"{sub.age}세" if sub.age is not None else "", conditions.age, sub.age),
            (JOB, sub.job_status.value, conditions.job, JOB_CODES.get(sub.job_status)),
            (SCHOOL, sub.school.value, conditions.school, SCHOOL_CODES.get(sub.school)),
        ]
        for name, question, profile, code in candidates:
            if code is not None and profile is not None and str(code) != str(profile.code):
                found.setdefault(name, Mismatch(name, question, profile.label))
    return [found[name] for name in (REGION, AGE, JOB, SCHOOL) if name in found]


# "기타"는 정해진 분류에 맞지 않는다는 뜻이라 그 분류 전용 정책을 빼면 맞는 정책까지 빠질 수 있어 거르지 않음
# "제한없음"(해당 없음)은 사용자가 그 대상이 아니라고 답한 것이라 그대로 거름
def _profile_code(code: str | None, code_names: dict[str, str]) -> Condition | None:
    if not code or code_names.get(code) == OTHER_CODE_NAME:
        return None
    return Condition(code, code_names.get(code, code))


def _lines(conditions: SearchConditions) -> list[str]:
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
        for mismatch in conditions.mismatches
    ]
    if conditions.unknown_region:
        lines.append(f"질문에서 말한 지역: {conditions.unknown_region} (어느 지역인지 알 수 없어 프로필 지역 기준으로 찾음)")
    return lines


def _short_sido_name(name: str) -> str:
    for suffix in SIDO_SUFFIXES:
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    # "충청북도" → "충북"처럼 네 글자 도는 첫 글자와 셋째 글자를 씀
    if name.endswith("도"):
        return name[0] + name[2] if len(name) == 4 else name.removesuffix("도")
    return name
