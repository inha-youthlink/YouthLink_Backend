import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.repositories.vector_repository import EMBEDDING_DIM, VectorRepository
from tests.conftest import TEST_POLICY_NOS

MODEL = "test-embedding"
ACTIVE, OTHER = TEST_POLICY_NOS[0], TEST_POLICY_NOS[1]
INACTIVE = "V-INACTIVE"


def _vector(first: float) -> list[float]:
    return [first, 1.0 - first] + [0.0] * (EMBEDDING_DIM - 2)


@pytest.fixture
async def repository(session_factory):
    # 비활성 정책을 질문과 가장 가까운 벡터로 둬서, 빠지지 않으면 맨 앞에 나옴
    chunks = [(INACTIVE, _vector(1.0)), (ACTIVE, _vector(0.9)), (OTHER, _vector(0.5))]
    async with session_factory() as session, session.begin():
        await session.execute(
            text("""INSERT INTO policy (policy_no, policy_name, is_active, deactivated_at)
                VALUES (:no, '사라진 정책', FALSE, :at)"""),
            {"no": INACTIVE, "at": datetime(2026, 10, 20, tzinfo=UTC)},
        )
        await session.execute(
            text("""INSERT INTO policy_chunk (policy_no, chunk_index, chunk_type, content, content_hash,
                    chunker_version, embedding, embedding_model, embedded_at)
                VALUES (:no, 0, 'SUMMARY', :content, :hash, 'test', CAST(:vec AS vector), :model, NOW())"""),
            [
                {"no": no, "content": f"{no} 요약", "hash": hashlib.sha256(no.encode()).hexdigest(),
                 "vec": str(vector), "model": MODEL}
                for no, vector in chunks
            ],
        )
    yield VectorRepository(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM policy_chunk"))
        await session.execute(text("DELETE FROM policy WHERE policy_no = :no"), {"no": INACTIVE})


async def test_search_excludes_inactive_policy(repository):
    chunks = await repository.search(_vector(1.0), MODEL, k=10)

    assert [chunk.policy_no for chunk in chunks] == [ACTIVE, OTHER]


async def test_search_with_policy_ids_excludes_inactive_policy(repository):
    chunks = await repository.search(_vector(1.0), MODEL, k=10, policy_ids=[INACTIVE, OTHER])

    assert [chunk.policy_no for chunk in chunks] == [OTHER]
