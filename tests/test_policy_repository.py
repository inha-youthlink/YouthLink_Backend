import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.repositories.policy_repository import PolicyRepository
from tests.conftest import TEST_POLICY_NOS

FIRST, SECOND, WITHOUT_CHUNKS = TEST_POLICY_NOS

_INSERT_CHUNK_SQL = text("""
INSERT INTO policy_chunk (policy_no, chunk_index, chunk_type, content, content_hash, chunker_version)
VALUES (:policy_no, :chunk_index, :chunk_type, :content, :content_hash, 'test')
""")


@pytest.fixture
async def policy_repository(session_factory):
    # 정책은 conftest가 넣어 두므로 청크만 넣고 지움. 순서를 확인하려고 뒤 청크부터 넣음
    chunks = [
        (FIRST, 1, "ELIGIBILITY", "첫 정책 자격"),
        (FIRST, 0, "SUMMARY", "첫 정책 요약"),
        (SECOND, 0, "SUMMARY", "둘째 정책 요약"),
    ]
    async with session_factory() as session, session.begin():
        await session.execute(_INSERT_CHUNK_SQL, [
            {
                "policy_no": policy_no,
                "chunk_index": index,
                "chunk_type": chunk_type,
                "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
            for policy_no, index, chunk_type, content in chunks
        ])
    yield PolicyRepository(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM policy_chunk"))


async def test_get_documents_returns_all_chunks_in_order(policy_repository):
    documents = await policy_repository.get_documents([FIRST, SECOND, WITHOUT_CHUNKS])

    assert documents == {
        FIRST: ["첫 정책 요약", "첫 정책 자격"],
        SECOND: ["둘째 정책 요약"],
    }


async def test_get_documents_empty_input(policy_repository):
    assert await policy_repository.get_documents([]) == {}


DETAIL_NO = "D-DETAIL"
DETAIL_CODES = [("0013003", "jobCd", "미취업자"), ("0013004", "jobCd", "프리랜서"),
                ("0011009", "plcyMajorCd", "제한없음"), ("0055002", "mrgSttsCd", "미혼")]


@pytest.fixture
async def detail_repository(session_factory):
    async with session_factory() as session, session.begin():
        await session.execute(
            text("""INSERT INTO common_code (code, code_group, code_name) VALUES (:code, :code_group, :code_name)
                ON CONFLICT DO NOTHING"""),
            [{"code": code, "code_group": group, "code_name": name} for code, group, name in DETAIL_CODES],
        )
        await session.execute(text("""
            INSERT INTO policy (policy_no, policy_name, description, support_content, application_method,
                                additional_qualification, min_age, max_age, income_condition_code, max_income,
                                marriage_status_code, supervising_org_name, operating_org_name, large_categories,
                                screening_method, additional_notes, first_come_first_served, application_period_raw,
                                synced_at)
            VALUES (:no, '상세 정책', '요약', '지원 내용', '온라인', '추가 자격', 19, 39, '0043002', 5000,
                    '0055002', '서울특별시', '서울청년센터', ARRAY['주거'],
                    '서류 심사', '중복 지원 불가', TRUE, '2026.10.01 ~ 2026.11.30', '2026-11-10T03:00:00+00:00')
        """), {"no": DETAIL_NO})
        await session.execute(
            text("""INSERT INTO policy_eligibility_code (policy_no, condition_type, condition_code)
                VALUES (:no, :type, :code)"""),
            [{"no": DETAIL_NO, "type": t, "code": c} for t, c in (("JOB", "0013004"), ("JOB", "0013003"), ("MAJOR", "0011009"))],
        )
    yield PolicyRepository(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM policy WHERE policy_no = :no"), {"no": DETAIL_NO})


async def test_get_detail(detail_repository):
    detail = await detail_repository.get_detail(DETAIL_NO)

    assert detail is not None
    assert (detail.record.policy_no, detail.record.policy_name, detail.record.large_categories) == (
        DETAIL_NO, "상세 정책", ["주거"],
    )
    assert (detail.record.supervising_org_name, detail.operating_org_name) == ("서울특별시", "서울청년센터")
    assert (detail.description, detail.support_content, detail.application_method, detail.submission_documents) == (
        "요약", "지원 내용", "온라인", None,
    )
    assert (detail.min_age, detail.max_age, detail.income_condition_code, detail.max_income) == (19, 39, "0043002", 5000)
    assert detail.marriage_name == "미혼"
    assert detail.is_active is True
    # 신청 가이드용
    assert (detail.screening_method, detail.additional_notes, detail.first_come_first_served) == (
        "서류 심사", "중복 지원 불가", True,
    )
    assert detail.application_period_raw == "2026.10.01 ~ 2026.11.30"
    assert detail.synced_at == datetime(2026, 11, 10, 3, tzinfo=UTC)
    # 코드 순서대로, 제한없음도 그대로 (거르는 것은 서비스에서)
    assert detail.eligibility_names == {"JOB": ["미취업자", "프리랜서"], "MAJOR": ["제한없음"]}


# 기본값이 활성이라 SQL이 is_active를 읽지 않아도 다른 테스트는 통과함. 비활성 정책으로 직접 확인함
async def test_get_detail_reads_inactive(detail_repository, session_factory):
    update = text("UPDATE policy SET is_active = :active, deactivated_at = :at WHERE policy_no = :no")
    async with session_factory() as session, session.begin():
        await session.execute(update, {"active": False, "at": datetime(2026, 10, 20, tzinfo=UTC), "no": DETAIL_NO})

    detail = await detail_repository.get_detail(DETAIL_NO)

    assert detail is not None
    assert detail.is_active is False


async def test_get_detail_unknown(detail_repository):
    assert await detail_repository.get_detail("D-NONE") is None


NAME_POLICIES = [
    ("N-SAVE", "청년 자산형성 지원(청년도약계좌)", True),
    ("N-SAVE-OLD", "청년도약계좌", False),
    ("N-JOB", "청년일자리도약장려금", True),
    ("N-PERCENT", "100% 환급 청년 지원", True),
]


@pytest.fixture
async def name_repository(session_factory):
    async with session_factory() as session, session.begin():
        await session.execute(
            text("""INSERT INTO policy (policy_no, policy_name, is_active, deactivated_at)
                VALUES (:no, :name, :active, :at)"""),
            [
                {"no": no, "name": name, "active": active, "at": None if active else datetime(2026, 10, 20, tzinfo=UTC)}
                for no, name, active in NAME_POLICIES
            ],
        )
    yield PolicyRepository(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM policy WHERE policy_no = ANY(:nos)"), {"nos": [no for no, _, _ in NAME_POLICIES]}
        )


# 공백을 빼고 비교해 "청년 도약계좌"도 붙여 쓴 이름에 걸리고, 비활성 정책은 빠짐
async def test_find_nos_by_name_ignores_spaces_and_inactive(name_repository):
    assert await name_repository.find_nos_by_name("청년 도약계좌", None, 10) == ["N-SAVE"]
    assert await name_repository.find_nos_by_name("도약", None, 10) == ["N-SAVE", "N-JOB"]


async def test_find_nos_by_name_within_filter_and_limit(name_repository):
    assert await name_repository.find_nos_by_name("도약", ["N-JOB"], 10) == ["N-JOB"]
    assert await name_repository.find_nos_by_name("도약", [], 10) == []
    assert await name_repository.find_nos_by_name("도약", None, 1) == ["N-SAVE"]


# %가 와일드카드로 읽히면 모든 정책이 걸림. 한 글자는 거의 모든 이름에 들어 있어 찾지 않음
async def test_find_nos_by_name_literal_and_too_short(name_repository):
    assert await name_repository.find_nos_by_name("0% 환급", None, 10) == ["N-PERCENT"]
    assert await name_repository.find_nos_by_name("%", None, 10) == []
    assert await name_repository.find_nos_by_name("청 ", None, 10) == []
