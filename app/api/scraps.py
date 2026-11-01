from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.dependencies import ClientId, get_policy_repository, get_scrap_repository
from app.repositories.policy_repository import PolicyRepository
from app.repositories.scrap_repository import ScrapRepository
from app.schemas.policy import Scrap, ScrapRequest
from app.schemas.public import ErrorResponse
from app.services.scrap_service import ScrapService

router = APIRouter(
    prefix="/api/scraps",
    tags=["scraps"],
    responses={status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}},
)


def get_scrap_service(
    scrap_repository: ScrapRepository = Depends(get_scrap_repository),
    policy_repository: PolicyRepository = Depends(get_policy_repository),
) -> ScrapService:
    return ScrapService(scrap_repository, policy_repository)


Service = Annotated[ScrapService, Depends(get_scrap_service)]


# 응답이 X-Client-Id마다 달라 공용 캐시가 다른 사용자의 목록을 돌려주지 않게 막음
@router.get("")
async def list_scraps(client_id: ClientId, service: Service, response: Response) -> list[Scrap]:
    response.headers["Cache-Control"] = "no-store"
    return await service.list_scraps(client_id)


# 계약 §4.5: 새로 만들면 201, 이미 있으면 200으로 기존 값
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {"model": Scrap, "description": "이미 스크랩한 정책"},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def add_scrap(request: ScrapRequest, client_id: ClientId, service: Service, response: Response) -> Scrap:
    scrap, created = await service.add_scrap(client_id, request.policy_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return scrap


# 없어도 204 (계약 멱등). 경로 값은 형식을 검사하지 않고 그대로 지움
@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_scrap(policy_id: str, client_id: ClientId, service: Service) -> Response:
    await service.remove_scrap(client_id, policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
