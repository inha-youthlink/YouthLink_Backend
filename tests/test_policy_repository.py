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
