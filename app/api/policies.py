from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import StringConstraints

from app.core.dependencies import get_client_id, get_policy_repository
from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy import KEYWORD_MAX_CHARS, PAGE_SIZE_MAX, SearchCategory
from app.schemas.public import ErrorResponse, Page
from app.schemas.session import PolicySummary
from app.services.policy_service import PolicyService

# 사용자별 응답이 아니라 Cache-Control은 붙이지 않음. X-Client-Id는 계약상 모든 요청에 있어 형식만 확인함
router = APIRouter(
    prefix="/api/policies",
    tags=["policies"],
    dependencies=[Depends(get_client_id)],
    responses={status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}},
)


def get_policy_service(policy_repository: PolicyRepository = Depends(get_policy_repository)) -> PolicyService:
    return PolicyService(policy_repository)


Service = Annotated[PolicyService, Depends(get_policy_service)]
# 공백만 있는 검색어는 검색어 없음으로 봄. 빈 값(keyword=, region=)도 조건 없음
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, max_length=KEYWORD_MAX_CHARS)]


@router.get("")
async def search_policies(
    service: Service,
    keyword: Annotated[Keyword | None, Query()] = None,
    category: Annotated[SearchCategory, Query()] = None,
    region: Annotated[str | None, Query(max_length=20)] = None,
    page: Annotated[int, Query(ge=0, le=10_000)] = 0,
    size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 20,
) -> Page[PolicySummary]:
    return await service.search(keyword or None, category, region or None, page, size)
