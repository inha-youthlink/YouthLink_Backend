from datetime import date

from app.core.errors import PolicyNotFoundError
from app.repositories.policy_repository import PolicyRepository
from app.repositories.scrap_repository import AddResult, ScrapRecord, ScrapRepository
from app.schemas.policy import Scrap
from app.schemas.policy_info import today_kst
from app.schemas.session import PolicySummary


class ScrapService:
    def __init__(self, scrap_repository: ScrapRepository, policy_repository: PolicyRepository) -> None:
        self._scrap_repository = scrap_repository
        self._policy_repository = policy_repository

    async def list_scraps(self, client_id: str) -> list[Scrap]:
        today = today_kst()
        return await self._to_scraps(await self._scrap_repository.list_scraps(client_id, today), today)

    # 새로 만들었는지(201/200)와 함께 돌려줌. 이미 있으면 처음 스크랩한 시각 그대로 (계약 멱등)
    async def add_scrap(self, client_id: str, policy_id: str) -> tuple[Scrap, bool]:
        result = await self._scrap_repository.add_scrap(client_id, policy_id)
        if result is AddResult.POLICY_NOT_FOUND:
            raise PolicyNotFoundError
        today = today_kst()
        record = await self._scrap_repository.get_scrap(client_id, policy_id, today)
        # 추가와 조회 사이에 정책이 비활성이 된 경우. 목록에서도 빠지므로 없는 정책과 같게 봄
        if record is None:
            raise PolicyNotFoundError
        return (await self._to_scraps([record], today))[0], result is AddResult.CREATED

    async def remove_scrap(self, client_id: str, policy_id: str) -> None:
        await self._scrap_repository.remove_scrap(client_id, policy_id)

    async def _to_scraps(self, records: list[ScrapRecord], today: date) -> list[Scrap]:
        labels = await self._policy_repository.get_region_labels([r.record.policy_no for r in records])
        return [
            Scrap(
                policy=PolicySummary.from_item(r.record.to_item(0.0, today, labels.get(r.record.policy_no))),
                created_at=r.created_at,
            )
            for r in records
        ]
