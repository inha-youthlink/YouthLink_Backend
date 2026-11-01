from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.policy_repository import (
    RECORD_COLUMNS,
    RECORD_FIELDS,
    STATUS_GROUP_SQL,
    STATUS_ORDER_SQL,
    PolicyRecord,
)
from app.schemas.policy_info import ALWAYS_OPEN_CODE, CLOSED_CODE

# 비활성 정책(원천에서 사라짐)은 상세가 404라 목록에서 뺌. 행은 남겨 정책이 다시 활성이 되면 목록에 돌아옴
_SCRAP_ROWS_SQL = f"""
SELECT {RECORD_COLUMNS}, s.created_at AS scrapped_at, {STATUS_GROUP_SQL} AS status_group
FROM policy_scrap s
JOIN policy p ON p.policy_no = s.policy_no
WHERE s.user_id = :user_id AND p.is_active
"""

# 정책 검색과 같은 신청 상태 순서. 같은 그룹·같은 날짜는 최근에 스크랩한 것 먼저 (계약 "나중에 답할 것")
_LIST_SQL = text(f"""
SELECT * FROM ({_SCRAP_ROWS_SQL}) matched
ORDER BY {STATUS_ORDER_SQL},
    scrapped_at DESC,
    policy_no DESC
""")

_GET_SQL = text(f"{_SCRAP_ROWS_SQL} AND s.policy_no = :policy_no")

_IS_ACTIVE_SQL = text("SELECT is_active FROM policy WHERE policy_no = :policy_no")

# 동시에 두 번 눌러도 한 건만 생기고, RETURNING이 비면 이미 있던 스크랩
_INSERT_SQL = text("""
INSERT INTO policy_scrap (user_id, policy_no)
VALUES (:user_id, :policy_no)
ON CONFLICT (user_id, policy_no) DO NOTHING
RETURNING created_at
""")

_DELETE_SQL = text("DELETE FROM policy_scrap WHERE user_id = :user_id AND policy_no = :policy_no")


class AddResult(Enum):
    CREATED = "created"
    EXISTS = "exists"
    # 없거나 비활성인 정책
    POLICY_NOT_FOUND = "policy_not_found"


@dataclass(frozen=True)
class ScrapRecord:
    record: PolicyRecord
    created_at: datetime


class ScrapRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_scraps(self, user_id: str, today: date) -> list[ScrapRecord]:
        async with self._session_factory() as session:
            result = await session.execute(_LIST_SQL, {"user_id": user_id, **_status_params(today)})
            return [_scrap(row) for row in result.mappings()]

    async def get_scrap(self, user_id: str, policy_no: str, today: date) -> ScrapRecord | None:
        async with self._session_factory() as session:
            params = {"user_id": user_id, "policy_no": policy_no, **_status_params(today)}
            row = (await session.execute(_GET_SQL, params)).mappings().first()
            return _scrap(row) if row else None

    async def add_scrap(self, user_id: str, policy_no: str) -> AddResult:
        async with self._session_factory() as session, session.begin():
            if not (await session.execute(_IS_ACTIVE_SQL, {"policy_no": policy_no})).scalar():
                return AddResult.POLICY_NOT_FOUND
            created_at = (await session.execute(_INSERT_SQL, {"user_id": user_id, "policy_no": policy_no})).scalar()
            return AddResult.CREATED if created_at is not None else AddResult.EXISTS

    # 비활성 정책이어도 지울 수 있음. 없어도 오류가 아님 (계약 멱등)
    async def remove_scrap(self, user_id: str, policy_no: str) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(_DELETE_SQL, {"user_id": user_id, "policy_no": policy_no})


def _status_params(today: date) -> dict[str, object]:
    return {"always_open_code": ALWAYS_OPEN_CODE, "closed_code": CLOSED_CODE, "today": today}


def _scrap(row: RowMapping) -> ScrapRecord:
    return ScrapRecord(
        record=PolicyRecord(**{key: value for key, value in row.items() if key in RECORD_FIELDS}),
        created_at=row["scrapped_at"],
    )
