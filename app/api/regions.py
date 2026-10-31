from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.policies import get_policy_service
from app.core.dependencies import get_client_id
from app.schemas.policy import SidoRegions
from app.services.policy_service import PolicyService

# 사용자별 응답이 아니라 Cache-Control은 붙이지 않음. X-Client-Id는 계약상 모든 요청에 있어 형식만 확인함
router = APIRouter(prefix="/api/regions", tags=["regions"], dependencies=[Depends(get_client_id)])


@router.get("")
async def list_regions(service: Annotated[PolicyService, Depends(get_policy_service)]) -> list[SidoRegions]:
    return await service.list_regions()
