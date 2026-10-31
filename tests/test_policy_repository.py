import hashlib

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
                                marriage_status_code, supervising_org_name, operating_org_name, large_categories)
            VALUES (:no, '상세 정책', '요약', '지원 내용', '온라인', '추가 자격', 19, 39, '0043002', 5000,
                    '0055002', '서울특별시', '서울청년센터', ARRAY['주거'])
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
    # 코드 순서대로, 제한없음도 그대로 (거르는 것은 서비스에서)
    assert detail.eligibility_names == {"JOB": ["미취업자", "프리랜서"], "MAJOR": ["제한없음"]}


async def test_get_detail_unknown(detail_repository):
    assert await detail_repository.get_detail("D-NONE") is None
