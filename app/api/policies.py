from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import StringConstraints

from app.core.dependencies import (
    ClientId,
    get_chat_repository,
    get_client_id,
    get_common_code_repository,
    get_policy_repository,
    parse_session_id,
)
from app.core.errors import ValidationFailedError
from app.repositories.chat_repository import ChatRepository
from app.repositories.common_code_repository import CommonCodeRepository
from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy import (
    KEYWORD_MAX_CHARS,
    PAGE_SIZE_MAX,
    PolicyDetail,
    SearchCategory,
)
from app.schemas.public import ErrorResponse, Page
from app.schemas.session import PolicySummary
from app.services.policy_service import PolicyService

# 세션 없는 검색·상세는 사용자별 응답이 아니라 Cache-Control은 붙이지 않음. 세션 검색만 X-Client-Id로 주인을 확인함
router = APIRouter(
    prefix="/api/policies",
    tags=["policies"],
    dependencies=[Depends(get_client_id)],
    responses={status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}},
)


def get_policy_service(
    policy_repository: PolicyRepository = Depends(get_policy_repository),
    chat_repository: ChatRepository = Depends(get_chat_repository),
    common_code_repository: CommonCodeRepository = Depends(get_common_code_repository),
) -> PolicyService:
    return PolicyService(policy_repository, chat_repository, common_code_repository)


Service = Annotated[PolicyService, Depends(get_policy_service)]
_NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}}
# 공백만 있는 검색어는 검색어 없음으로 봄. 빈 값(keyword=, region=)도 조건 없음
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, max_length=KEYWORD_MAX_CHARS)]


@router.get("", responses=_NOT_FOUND)
async def search_policies(
    service: Service,
    client_id: ClientId,
    response: Response,
    keyword: Annotated[Keyword | None, Query()] = None,
    category: Annotated[SearchCategory, Query()] = None,
    region: Annotated[str | None, Query(max_length=20)] = None,
    session_id: Annotated[str | None, Query(alias="sessionId")] = None,
    page: Annotated[int, Query(ge=0, le=10_000)] = 0,
    size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 20,
) -> Page[PolicySummary]:
    parsed_session_id = None
    if session_id:
        # 세션 검색은 프로필 지역으로 거르므로 다른 지역을 함께 받으면 어느 쪽인지 알 수 없음
        if region:
            raise ValidationFailedError("region", "sessionId와 함께 쓸 수 없습니다.")
        parsed_session_id = parse_session_id(session_id)
        # 결과가 세션 프로필마다 달라 공용 캐시가 다른 사용자에게 돌려주지 않게 막음
        response.headers["Cache-Control"] = "no-store"
    return await service.search(
        keyword or None, category, region or None, page, size, client_id=client_id, session_id=parsed_session_id
    )


@router.get("/{policy_id}", responses=_NOT_FOUND)
async def get_policy(policy_id: str, service: Service) -> PolicyDetail:
    return await service.get_detail(policy_id)
