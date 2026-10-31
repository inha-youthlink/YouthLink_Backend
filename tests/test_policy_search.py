from datetime import date

import pytest
from sqlalchemy import text

from app.repositories.policy_repository import PolicyFilter, PolicyRepository
from app.schemas.policy_info import (
    ALWAYS_OPEN_CODE,
    CLOSED_CODE,
    ApplicationStatus,
    application_status,
)

TODAY = date(2026, 10, 23)
PERIOD_CODE = "0057001"
REGIONS = [
    ("11110", "11", "110", "서울특별시", "종로구"),
    ("11680", "11", "680", "서울특별시", "강남구"),
    ("28237", "28", "237", "인천광역시", "부평구"),
]
SEOUL = ["11110", "11680"]
NATION = [region[0] for region in REGIONS]


def _period(start: date | None, end: date | None) -> dict:
    return {
        "application_period_type_code": PERIOD_CODE,
        "application_start_date": start,
        "application_end_date": end,
    }


# policy_no: 지역 코드, 그 밖의 컬럼. 정책 번호 뒤 숫자가 클수록 최신 정책
POLICIES = {
    "S-OPEN-LATE-01": (NATION, _period(date(2026, 10, 1), date(2026, 12, 31))),
    "S-OPEN-SOON-02": (NATION, _period(date(2026, 10, 1), date(2026, 10, 25))),
    # 끝나는 날이 없는 진행 중은 진행 중의 맨 뒤
    "S-OPEN-NOEND-03": (NATION, _period(date(2026, 10, 1), None)),
    "S-ALWAYS-04": (NATION, {"application_period_type_code": ALWAYS_OPEN_CODE}),
    "S-ALWAYS-05": (NATION, {"application_period_type_code": ALWAYS_OPEN_CODE}),
    "S-UPCOMING-06": (NATION, _period(date(2026, 11, 20), date(2026, 12, 20))),
    "S-UPCOMING-07": (NATION, _period(date(2026, 11, 1), date(2026, 11, 30))),
    "S-UNKNOWN-08": (NATION, {"application_period_type_code": PERIOD_CODE}),
    "S-CLOSED-OLD-09": (NATION, _period(date(2026, 1, 1), date(2026, 3, 31))),
    "S-CLOSED-NEW-10": (NATION, _period(date(2026, 9, 1), date(2026, 10, 22))),
    # 마감 코드는 기간이 남아 있어도 마감
    "S-CLOSED-CODE-11": (NATION, {"application_period_type_code": CLOSED_CODE, "application_end_date": date(2026, 12, 1)}),
}
EXPECTED_ORDER = [
    "S-OPEN-SOON-02", "S-OPEN-LATE-01", "S-OPEN-NOEND-03",
    "S-UPCOMING-07", "S-UPCOMING-06",
    "S-ALWAYS-05", "S-ALWAYS-04",
    "S-UNKNOWN-08",
    "S-CLOSED-CODE-11", "S-CLOSED-NEW-10", "S-CLOSED-OLD-09",
]
# 검색어·분류·지역 확인용 (진행 중이라 정렬에서 맨 앞 그룹)
TEXT_POLICIES = {
    "T-RENT": (SEOUL, {"policy_name": "청년 월세 지원", "large_categories": ["주거"], "keywords": ["주거지원"]}),
    "T-JOB": (["28237"], {"policy_name": "구직 활동 수당", "description": "취업 준비 청년의 월세 부담 완화",
                          "large_categories": ["일자리", "주거"]}),
    "T-LOAN": (["11680"], {"policy_name": "전세 대출", "large_categories": ["금융･복지･문화"],
                           "keywords": ["청년월세", "100%_보증"]}),
}


@pytest.fixture
async def repository(session_factory):
    open_period = _period(date(2026, 10, 1), date(2026, 12, 31))
    rows = {
        **POLICIES,
        **{no: (codes, {**open_period, **columns}) for no, (codes, columns) in TEXT_POLICIES.items()},
    }
    async with session_factory() as session, session.begin():
        await session.execute(
            text("""INSERT INTO region (region_code, sido_code, sigungu_code, sido_name, sigungu_name, region_name)
                VALUES (:code, :sido, :sigungu, :sido_name, :sigungu_name, :region_name)"""),
            [
                {**dict(zip(("code", "sido", "sigungu", "sido_name", "sigungu_name"), row)),
                 "region_name": f"{row[3]} {row[4]}"}
                for row in REGIONS
            ],
        )
        for policy_no, (codes, columns) in rows.items():
            columns = {"policy_name": policy_no, **columns}
            names = ["policy_no", *columns]
            await session.execute(
                text(f"INSERT INTO policy ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})"),
                {"policy_no": policy_no, **columns},
            )
            await session.execute(
                text("INSERT INTO policy_region (policy_no, region_code) VALUES (:policy_no, :code)"),
                [{"policy_no": policy_no, "code": code} for code in codes],
            )
    yield PolicyRepository(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM policy WHERE policy_no LIKE 'S-%' OR policy_no LIKE 'T-%'"))
        await session.execute(text("DELETE FROM region"))


async def _nos(repository, prefix: str, offset: int = 0, limit: int = 100, **conditions) -> tuple[list[str], int]:
    records, total = await repository.search(PolicyFilter(**conditions), TODAY, offset, limit)
    return [record.policy_no for record in records if record.policy_no.startswith(prefix)], total


async def test_order_by_status_then_deadline(repository):
    nos, _ = await _nos(repository, "S-")

    assert nos == EXPECTED_ORDER


# 정렬 그룹이 카드의 신청 상태와 같은 규칙인지 확인
async def test_order_groups_match_application_status(repository):
    records, _ = await repository.search(PolicyFilter(), TODAY, 0, 100)
    statuses = [
        application_status(r.application_period_type_code, r.application_start_date, r.application_end_date, TODAY)
        for r in records
        if r.policy_no.startswith("S-")
    ]

    order = [ApplicationStatus.OPEN, ApplicationStatus.UPCOMING, ApplicationStatus.ALWAYS_OPEN,
             ApplicationStatus.UNKNOWN, ApplicationStatus.CLOSED]
    assert statuses == sorted(statuses, key=order.index)


async def test_paging_and_total(repository):
    first, total = await repository.search(PolicyFilter(), TODAY, 0, 4)
    second, _ = await repository.search(PolicyFilter(), TODAY, 4, 4)
    beyond, beyond_total = await repository.search(PolicyFilter(), TODAY, 100, 4)

    every, _ = await repository.search(PolicyFilter(), TODAY, 0, 100)
    assert [r.policy_no for r in first + second] == [r.policy_no for r in every[:8]]
    # 범위를 넘은 페이지도 전체 개수는 알려 줌
    assert total == beyond_total == len(every) >= len(POLICIES) + len(TEXT_POLICIES)
    assert beyond == []


async def test_keyword_matches_name_description_and_keywords(repository):
    # 이름(T-RENT), 설명(T-JOB), 키워드 일부(T-LOAN "청년월세")
    nos, total = await _nos(repository, "T-", keywords=("월세",))

    assert set(nos) == {"T-RENT", "T-JOB", "T-LOAN"}
    assert total == 3


async def test_keyword_words_must_all_match(repository):
    nos, _ = await _nos(repository, "T-", keywords=("월세", "청년"))
    assert set(nos) == {"T-RENT", "T-JOB", "T-LOAN"}

    nos, _ = await _nos(repository, "T-", keywords=("월세", "구직"))
    assert nos == ["T-JOB"]


async def test_keyword_special_characters_are_literal(repository):
    nos, _ = await _nos(repository, "T-", keywords=("100%_보증",))
    assert nos == ["T-LOAN"]

    # %·_가 와일드카드로 읽히면 모든 정책이 걸림. 글자 그대로 가진 T-LOAN만 걸려야 함
    assert await _nos(repository, "", keywords=("%",)) == (["T-LOAN"], 1)
    assert await _nos(repository, "", keywords=("_",)) == (["T-LOAN"], 1)
    assert await _nos(repository, "", keywords=("\\",)) == ([], 0)


async def test_category_matches_any_large_category(repository):
    nos, _ = await _nos(repository, "T-", large_categories=("주거",))

    # 대분류가 여러 개인 정책도 걸림
    assert set(nos) == {"T-RENT", "T-JOB"}


async def test_region_and_keyword_together(repository):
    nos, _ = await _nos(repository, "T-", keywords=("월세",), sido_code="11", region_codes=("11680",))

    # 인천 전용(T-JOB)만 빠짐
    assert set(nos) == {"T-RENT", "T-LOAN"}
