from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.pipeline import Chunk

# ETL policy_chunk.embedding VECTOR(1536)과 같아야 함
EMBEDDING_DIM = 1536

# embedding IS NOT NULL: HNSW가 이 조건의 조건부 인덱스라 빠지면 인덱스를 쓰지 못함
# ORDER BY: score로 정렬하면 인덱스를 쓰지 못해 거리로 정렬함
_SEARCH_SQL = """
SELECT chunk_id, policy_no, chunk_index, chunk_type, content,
       1 - (embedding <=> :query_vec) AS score
FROM policy_chunk
WHERE embedding_model = :model
  AND embedding IS NOT NULL
  {policy_filter}
ORDER BY embedding <=> :query_vec
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
            policy_filter = "AND policy_no = ANY(:policy_ids)"
            params["policy_ids"] = policy_ids

        stmt = text(_SEARCH_SQL.format(policy_filter=policy_filter)).bindparams(
            bindparam("query_vec", type_=Vector(EMBEDDING_DIM)),
        )

        async with self._session_factory() as session:
            result = await session.execute(stmt, params)
            return [Chunk.model_validate(row) for row in result.mappings()]
