from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.pipeline import Chunk

# ETL policy_chunk.embedding VECTOR(1024)와 같아야 함
EMBEDDING_DIM = 1024

# embedding IS NOT NULL: HNSW가 이 조건의 조건부 인덱스라 빠지면 인덱스를 쓰지 못함
# ORDER BY: score로 정렬하면 인덱스를 쓰지 못해 거리로 정렬함
# 비활성 정책: ETL이 원천에서 사라진 정책의 기존 벡터를 지우지 않고 남겨 두어 여기서 뺌
_SEARCH_SQL = """
SELECT c.chunk_id, c.policy_no, c.chunk_index, c.chunk_type, c.content,
       1 - (c.embedding <=> :query_vec) AS score
FROM policy_chunk c
WHERE c.embedding_model = :model
  AND c.embedding IS NOT NULL
  AND EXISTS (SELECT 1 FROM policy p WHERE p.policy_no = c.policy_no AND p.is_active)
  {policy_filter}
ORDER BY c.embedding <=> :query_vec
LIMIT :k
"""


class VectorRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def search(
        self,
        embedding: list[float],
        embedding_model: str,
        k: int,
        policy_ids: list[str] | None = None,
    ) -> list[Chunk]:
        # None은 필터 없음, 빈 목록은 조건에 맞는 정책이 없다는 뜻이라 조회하지 않음
        if policy_ids is not None and not policy_ids:
            return []

        params = {"query_vec": embedding, "model": embedding_model, "k": k}
        policy_filter = ""
        if policy_ids is not None:
            policy_filter = "AND c.policy_no = ANY(:policy_ids)"
            params["policy_ids"] = policy_ids

        stmt = text(_SEARCH_SQL.format(policy_filter=policy_filter)).bindparams(
            bindparam("query_vec", type_=Vector(EMBEDDING_DIM)),
        )

        async with self._session_factory() as session, session.begin():
            # 조건(활성 정책, 정책 번호)을 HNSW 탐색 뒤에 적용하면 k개보다 적게 나올 수 있어 반복 탐색으로 k개를 채움
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            result = await session.execute(stmt, params)
            return [Chunk.model_validate(row) for row in result.mappings()]
