from datetime import date

import pytest
from sqlalchemy import text

from app.repositories.policy_repository import PolicyFilter, PolicyRepository
from app.schemas.policy_info import ALWAYS_OPEN_CODE, CLOSED_CODE

TODAY = date(2026, 10, 23)
PERIOD_CODE = "0057001"
UNEMPLOYED, EMPLOYED, JOB_ANY, JOB_OTHER = "0013003", "0013001", "0013010", "0013009"

REGIONS = [
    ("11110", "11", "110", "서울특별시", "종로구"),
    ("11680", "11", "680", "서울특별시", "강남구"),
    ("28237", "28", "237", "인천광역시", "부평구"),
]
# policy_no: 지역 코드 목록, 그 밖의 컬럼
POLICIES = {
    "F-SEOUL": (["11110", "11680"], {}),
    "F-GANGNAM": (["11680"], {"min_age": 19, "max_age": 34}),
    "F-INCHEON": (["28237"], {}),
    "F-NATION": (["11110", "11680", "28237"], {"income_condition_code": "0043002", "max_income": 2500}),
    "F-CLOSED": (["11110", "11680", "28237"], {"application_period_type_code": CLOSED_CODE}),
    "F-UPCOMING": (["11110", "11680", "28237"], {
        "application_period_type_code": PERIOD_CODE,
        "application_start_date": date(2026, 11, 1), "application_end_date": date(2026, 11, 30),
    }),
    "F-ALWAYS": (["11110", "11680", "28237"], {"application_period_type_code": ALWAYS_OPEN_CODE}),
    "F-MARRIED": (["11110", "11680", "28237"], {"marriage_status_code": "0055001"}),
}
JOB_CODES = {"F-SEOUL": [UNEMPLOYED], "F-INCHEON": [EMPLOYED], "F-ALWAYS": [JOB_ANY], "F-MARRIED": [JOB_OTHER]}


@pytest.fixture
async def repository(session_factory):
    async with session_factory() as session, session.begin():
        await session.execute(
            text("""INSERT INTO common_code (code, code_group, code_name) VALUES
                (:unemployed, 'jobCd', '미취업자'), (:employed, 'jobCd', '재직자'),
                (:any, 'jobCd', '제한없음'), (:other, 'jobCd', '기타')
                ON CONFLICT DO NOTHING"""),
            {"unemployed": UNEMPLOYED, "employed": EMPLOYED, "any": JOB_ANY, "other": JOB_OTHER},
        )
        await session.execute(
            text("""INSERT INTO region (region_code, sido_code, sigungu_code, sido_name, sigungu_name, region_name)
                VALUES (:code, :sido, :sigungu, :sido_name, :sigungu_name, :region_name)"""),
            [
                {**dict(zip(("code", "sido", "sigungu", "sido_name", "sigungu_name"), row)),
                 "region_name": f"{row[3]} {row[4]}"}
                for row in REGIONS
            ],
        )
        for policy_no, (codes, columns) in POLICIES.items():
            names = ["policy_no", "policy_name", *columns]
            await session.execute(
                text(f"INSERT INTO policy ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})"),
                {"policy_no": policy_no, "policy_name": policy_no, **columns},
            )
            await session.execute(
                text("INSERT INTO policy_region (policy_no, region_code) VALUES (:policy_no, :code)"),
                [{"policy_no": policy_no, "code": code} for code in codes],
            )
        for policy_no, codes in JOB_CODES.items():
            await session.execute(
                text("""INSERT INTO policy_eligibility_code (policy_no, condition_type, condition_code)
                    VALUES (:policy_no, 'JOB', :code)"""),
                [{"policy_no": policy_no, "code": code} for code in codes],
            )
    yield PolicyRepository(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM policy WHERE policy_no LIKE 'F-%'"))
        await session.execute(text("DELETE FROM region"))


async def _filter(repository, **conditions) -> set[str]:
    nos = await repository.filter_policy_nos(PolicyFilter(**conditions), TODAY)
    return {no for no in nos if no.startswith("F-")}


ALL = set(POLICIES)
NATIONWIDE = {"F-NATION", "F-CLOSED", "F-UPCOMING", "F-ALWAYS", "F-MARRIED"}


async def test_no_conditions_keep_everything(repository):
    assert await _filter(repository) == ALL


async def test_region_keeps_sido_and_nationwide(repository):
    assert await _filter(repository, sido_code="11") == NATIONWIDE | {"F-SEOUL", "F-GANGNAM"}
    assert await _filter(repository, sido_code="28") == NATIONWIDE | {"F-INCHEON"}


async def test_age_excludes_only_out_of_range(repository):
    # 나이 정보가 없는 정책은 남김
    assert await _filter(repository, age=40) == ALL - {"F-GANGNAM"}
    assert await _filter(repository, age=27) == ALL


async def test_job_code_keeps_match_unrestricted_other_and_unknown(repository):
    # 재직자 전용(F-INCHEON)만 빠지고, 제한없음·기타·조건 행 없음은 남음
    assert await _filter(repository, eligibility_codes={"JOB": UNEMPLOYED}) == ALL - {"F-INCHEON"}


async def test_marriage_excludes_other_status_only(repository):
    assert await _filter(repository, marriage_code="0055002") == ALL - {"F-MARRIED"}


async def test_income_uses_upper_limit_only(repository):
    # 상한 2,500만 원 정책만 연소득 3천만 원에서 빠짐
    assert await _filter(repository, annual_income_won=30_000_000) == ALL - {"F-NATION"}
    assert await _filter(repository, annual_income_won=20_000_000) == ALL


async def test_open_only_excludes_closed_and_upcoming(repository):
    assert await _filter(repository, exclude_closed=True, exclude_upcoming=True) == ALL - {"F-CLOSED", "F-UPCOMING"}


async def test_region_labels(repository):
    labels = await repository.get_region_labels(["F-SEOUL", "F-GANGNAM", "F-NATION"])

    assert labels == {"F-SEOUL": "서울특별시", "F-GANGNAM": "서울특별시 강남구", "F-NATION": "전국"}


async def test_list_regions(repository):
    rows = await repository.list_regions()

    assert {(row.sido_code, row.sigungu_name) for row in rows} == {("11", "종로구"), ("11", "강남구"), ("28", "부평구")}


async def test_nationwide_policy_nos(repository):
    nationwide = await repository.nationwide_policy_nos()

    assert {no for no in nationwide if no.startswith("F-")} == NATIONWIDE
