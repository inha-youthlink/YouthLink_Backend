import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.policy_info import ALWAYS_OPEN_CODE, CLOSED_CODE

NO_RESTRICTION_MARRIAGE_CODE = "0055003"
ANNUAL_INCOME_CODE = "0043002"
# 정책 소득 상한은 만 원 단위인데 원 단위로 들어간 값이 섞여 있어(예: 43,056,240) 이 이상은 판정하지 않음
MAX_INCOME_MANWON = 100_000
# 정책 지역은 ETL 동기화 때만 바뀌고, 전국 판정은 전체 정책을 집계해 느려(약 0.2초) 잠시 캐시함
NATIONWIDE_CACHE_SECONDS = 3600

_GET_BY_IDS_SQL = text("""
SELECT policy_no, policy_name,
       application_period_type_code, application_start_date, application_end_date,
       application_url, reference_url_1, reference_url_2, supervising_org_name
FROM policy
WHERE policy_no = ANY(:policy_nos)
""")

_LIST_REGIONS_SQL = text("""
SELECT DISTINCT sido_code, sido_name, sigungu_name
FROM region
WHERE is_active
""")

# 모든 시도에 걸친 정책은 시군구 몇 곳이 빠져도 전국으로 봄 (지역 칸을 차지하지 않게)
_NATIONWIDE_SQL = text("""
SELECT policy_no
FROM v_policy_region_resolved
GROUP BY policy_no
HAVING count(DISTINCT sido_code) = (SELECT count(DISTINCT sido_code) FROM region WHERE is_active)
""")

_GET_REGION_SCOPES_SQL = text("""
SELECT s.policy_no, r.region_scope, s.sido_name, s.sido_scope, s.resolved_active_region_count,
       (SELECT count(DISTINCT sido_code) FROM region WHERE is_active) AS total_sidos,
       (SELECT min(v.region_name) FROM v_policy_region_resolved v
        WHERE v.policy_no = s.policy_no AND v.sido_code = s.sido_code AND v.is_active) AS region_name
FROM v_policy_sido_scope s
JOIN v_policy_region_scope r USING (policy_no)
WHERE s.policy_no = ANY(:policy_nos)
ORDER BY s.policy_no, s.sido_code
""")

# 확실히 안 맞는 정책만 빼므로 정보가 없거나 "제한없음"·"기타"인 정책은 남김
_CODE_CONDITION_SQL = """(
    NOT EXISTS (SELECT 1 FROM policy_eligibility_code e
                WHERE e.policy_no = p.policy_no AND e.condition_type = :{type_param})
    OR EXISTS (SELECT 1 FROM policy_eligibility_code e JOIN common_code c ON c.code = e.condition_code
               WHERE e.policy_no = p.policy_no AND e.condition_type = :{type_param}
                 AND (e.condition_code = :{code_param} OR c.code_name IN ('제한없음', '기타')))
)"""

_GET_DOCUMENTS_SQL = text("""
SELECT policy_no, content
FROM policy_chunk
WHERE policy_no = ANY(:policy_nos)
ORDER BY policy_no, chunk_index
""")


@dataclass(frozen=True)
class RegionRow:
    sido_code: str
    sido_name: str
    sigungu_name: str


# 저장소 계층의 필터 조건. 값이 None이면 그 조건으로 거르지 않음
@dataclass(frozen=True)
class PolicyFilter:
    sido_code: str | None = None
    age: int | None = None
    # condition_type(JOB, SCHOOL, MAJOR, SPECIAL) → 사용자 코드
    eligibility_codes: dict[str, str] = field(default_factory=dict)
    marriage_code: str | None = None
    annual_income_won: int | None = None
    exclude_closed: bool = False
    exclude_upcoming: bool = False

    def is_empty(self) -> bool:
        return self == PolicyFilter()


@dataclass(frozen=True)
class PolicyRecord:
    policy_no: str
    policy_name: str
    application_period_type_code: str | None
    application_start_date: date | None
    application_end_date: date | None
    application_url: str | None
    reference_url_1: str | None
    reference_url_2: str | None
    supervising_org_name: str | None


class PolicyRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._nationwide: frozenset[str] | None = None
        self._nationwide_loaded_at = 0.0

    # ANY는 결과 순서를 보장하지 않아 dict로 반환함. 호출 측에서 묶기 결과 순서대로 꺼내 씀
    async def get_by_ids(self, policy_nos: list[str]) -> dict[str, PolicyRecord]:
        if not policy_nos:
            return {}

        async with self._session_factory() as session:
            result = await session.execute(_GET_BY_IDS_SQL, {"policy_nos": policy_nos})
            return {row["policy_no"]: PolicyRecord(**row) for row in result.mappings()}

    async def list_regions(self) -> list[RegionRow]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_REGIONS_SQL)
            return [RegionRow(**row) for row in result.mappings()]

    async def filter_policy_nos(self, policy_filter: PolicyFilter, today: date) -> list[str]:
        sql, params = _build_filter_sql(policy_filter, today)
        async with self._session_factory() as session:
            result = await session.execute(text(sql), params)
            return list(result.scalars())

    async def nationwide_policy_nos(self) -> frozenset[str]:
        if self._nationwide is None or time.monotonic() - self._nationwide_loaded_at > NATIONWIDE_CACHE_SECONDS:
            async with self._session_factory() as session:
                self._nationwide = frozenset((await session.execute(_NATIONWIDE_SQL)).scalars())
            self._nationwide_loaded_at = time.monotonic()
        return self._nationwide

    async def get_region_labels(self, policy_nos: list[str]) -> dict[str, str]:
        if not policy_nos:
            return {}
        async with self._session_factory() as session:
            return await fetch_region_labels(session, policy_nos)

    # 청크가 정책 원본 전체를 풀어 쓴 것이라, 검색된 청크만이 아니라 정책 전체를 근거로 씀
    async def get_documents(self, policy_nos: list[str]) -> dict[str, list[str]]:
        if not policy_nos:
            return {}

        async with self._session_factory() as session:
            result = await session.execute(_GET_DOCUMENTS_SQL, {"policy_nos": policy_nos})
            documents: dict[str, list[str]] = {}
            for row in result.mappings():
                documents.setdefault(row["policy_no"], []).append(row["content"])
            return documents


# 기록 복원(chat_repository)도 같은 표시를 쓰도록 세션을 받는 함수로 둠
async def fetch_region_labels(session: AsyncSession, policy_nos: list[str]) -> dict[str, str]:
    result = await session.execute(_GET_REGION_SCOPES_SQL, {"policy_nos": policy_nos})
    by_policy: dict[str, list[Any]] = {}
    for row in result.mappings():
        by_policy.setdefault(row["policy_no"], []).append(row)
    return {policy_no: _region_label(rows) for policy_no, rows in by_policy.items()}


def _region_label(rows: list[Any]) -> str:
    if rows[0]["region_scope"] == "NATIONWIDE" or len(rows) == rows[0]["total_sidos"]:
        return "전국"
    if len(rows) > 1:
        return f"{rows[0]['sido_name']} 외 {len(rows) - 1}개 시도"
    row = rows[0]
    if row["sido_scope"] == "SIDO_WIDE":
        return row["sido_name"]
    if row["resolved_active_region_count"] == 1 and row["region_name"]:
        return row["region_name"]
    return f"{row['sido_name']} {row['resolved_active_region_count']}곳"


# 조건이 있는 것만 붙여 asyncpg가 타입을 알 수 없는 NULL 비교 파라미터를 피함
def _build_filter_sql(policy_filter: PolicyFilter, today: date) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if policy_filter.sido_code:
        # 전국 정책도 모든 시군구 코드를 가지므로 그 시도의 코드가 하나라도 있으면 남김
        # 범위 View(v_policy_*_scope)는 전체 정책을 집계해 느려(약 0.3초) 정책별 해석 View를 씀
        clauses.append("""EXISTS (
    SELECT 1 FROM v_policy_region_resolved v
    WHERE v.policy_no = p.policy_no AND v.sido_code = :sido_code
)""")
        params["sido_code"] = policy_filter.sido_code

    if policy_filter.age is not None:
        clauses.append("(p.min_age IS NULL OR p.min_age <= :age) AND (p.max_age IS NULL OR p.max_age >= :age)")
        params["age"] = policy_filter.age

    for index, (condition_type, code) in enumerate(sorted(policy_filter.eligibility_codes.items())):
        type_param, code_param = f"type_{index}", f"code_{index}"
        clauses.append(_CODE_CONDITION_SQL.format(type_param=type_param, code_param=code_param))
        params[type_param], params[code_param] = condition_type, code

    if policy_filter.marriage_code:
        clauses.append(
            "(p.marriage_status_code IS NULL OR p.marriage_status_code IN (:marriage_code, :no_restriction_marriage))"
        )
        params["marriage_code"] = policy_filter.marriage_code
        params["no_restriction_marriage"] = NO_RESTRICTION_MARRIAGE_CODE

    # 부부 합산 기준이어도 본인 소득만으로 상한을 넘으면 확실히 해당되지 않아 상한만 봄
    if policy_filter.annual_income_won is not None:
        clauses.append("""(
    p.income_condition_code IS DISTINCT FROM :annual_income_code
    OR p.max_income IS NULL OR p.max_income <= 0 OR p.max_income >= :max_income_manwon
    OR p.max_income * 10000 >= :annual_income_won
)""")
        params["annual_income_code"] = ANNUAL_INCOME_CODE
        params["max_income_manwon"] = MAX_INCOME_MANWON
        params["annual_income_won"] = policy_filter.annual_income_won

    # 기간이 없는 정책(정보 없음)은 NULL 비교로 함께 빠지지 않도록 COALESCE로 감쌈
    if policy_filter.exclude_closed:
        clauses.append("""NOT COALESCE(
    p.application_period_type_code = :closed_code
    OR (p.application_period_type_code IS DISTINCT FROM :always_open_code AND p.application_end_date < :today),
    FALSE
)""")
    if policy_filter.exclude_upcoming:
        clauses.append("""NOT COALESCE(
    p.application_period_type_code IS DISTINCT FROM :always_open_code
    AND p.application_period_type_code IS DISTINCT FROM :closed_code
    AND p.application_start_date > :today,
    FALSE
)""")
    if policy_filter.exclude_closed or policy_filter.exclude_upcoming:
        params.update(closed_code=CLOSED_CODE, always_open_code=ALWAYS_OPEN_CODE, today=today)

    where = "\nAND ".join(clauses) if clauses else "TRUE"
    return f"SELECT p.policy_no FROM policy p\nWHERE {where}", params
