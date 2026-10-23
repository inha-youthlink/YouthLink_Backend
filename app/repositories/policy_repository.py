from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_GET_BY_IDS_SQL = text("""
SELECT policy_no, policy_name,
       application_period_type_code, application_start_date, application_end_date,
       application_url
FROM policy
WHERE policy_no = ANY(:policy_nos)
""")

_GET_DOCUMENTS_SQL = text("""
SELECT policy_no, content
FROM policy_chunk
WHERE policy_no = ANY(:policy_nos)
ORDER BY policy_no, chunk_index
""")


@dataclass(frozen=True)
class PolicyRecord:
    policy_no: str
    policy_name: str
    application_period_type_code: str | None
    application_start_date: date | None
    application_end_date: date | None
    application_url: str | None


class PolicyRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ANY는 결과 순서를 보장하지 않아 dict로 반환함. 호출 측에서 묶기 결과 순서대로 꺼내 씀
    async def get_by_ids(self, policy_nos: list[str]) -> dict[str, PolicyRecord]:
        if not policy_nos:
            return {}

        async with self._session_factory() as session:
            result = await session.execute(_GET_BY_IDS_SQL, {"policy_nos": policy_nos})
            return {row["policy_no"]: PolicyRecord(**row) for row in result.mappings()}

    # ETL 청크가 정책 원본 필드를 지역명·코드명까지 풀어 쓴 것이라, 정책 전체 근거로 검색 여부와 관계없이 모든 청크를 씀
    async def get_documents(self, policy_nos: list[str]) -> dict[str, list[str]]:
        if not policy_nos:
            return {}

        async with self._session_factory() as session:
            result = await session.execute(_GET_DOCUMENTS_SQL, {"policy_nos": policy_nos})
            documents: dict[str, list[str]] = {}
            for row in result.mappings():
                documents.setdefault(row["policy_no"], []).append(row["content"])
            return documents
