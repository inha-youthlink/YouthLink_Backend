import hmac
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.core.errors import InvalidClientIdError
from app.core.rate_limit import RateLimitedError, RateLimits
from app.llm.client import LLMClient
from app.pipeline.retrieval.base import Retriever
from app.repositories.chat_repository import ChatRepository
from app.repositories.common_code_repository import CommonCodeRepository
from app.repositories.policy_repository import PolicyRepository


# VectorRepository는 넣지 않음. 파이프라인은 검색기를 통해서만 검색함
@dataclass(frozen=True)
class PipelineDeps:
    settings: Settings
    llm: LLMClient
    retriever: Retriever
    policy_repository: PolicyRepository
    common_code_repository: CommonCodeRepository


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


def get_retriever(request: Request) -> Retriever:
    return request.app.state.retriever


def get_policy_repository(request: Request) -> PolicyRepository:
    return request.app.state.policy_repository


def get_common_code_repository(request: Request) -> CommonCodeRepository:
    return request.app.state.common_code_repository


def get_chat_repository(request: Request) -> ChatRepository:
    return request.app.state.chat_repository


def get_pipeline_deps(
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm),
    retriever: Retriever = Depends(get_retriever),
    policy_repository: PolicyRepository = Depends(get_policy_repository),
    common_code_repository: CommonCodeRepository = Depends(get_common_code_repository),
) -> PipelineDeps:
    return PipelineDeps(
        settings=settings,
        llm=llm,
        retriever=retriever,
        policy_repository=policy_repository,
        common_code_repository=common_code_repository,
    )


# 헤더가 없을 때도 검증 오류가 아니라 계약의 INVALID_CLIENT_ID로 응답하려고 직접 검사함
# 같은 ID를 대소문자·하이픈 없는 형태로 보내도 한 사용자로 묶이게 표준 형태로 저장함
def get_client_id(x_client_id: Annotated[str | None, Header()] = None) -> str:
    try:
        client_id = UUID(x_client_id or "")
    except ValueError:
        raise InvalidClientIdError from None
    if client_id.version != 4:
        raise InvalidClientIdError
    return str(client_id)


ClientId = Annotated[str, Depends(get_client_id)]


# /internal은 평가 서버 전용이라 FastAPI 기본 오류 형식을 그대로 씀
# 비교 시간으로 키를 한 글자씩 알아내지 못하게 compare_digest로 비교함
def verify_internal_key(
    x_internal_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.internal_api_key is None:
        return
    expected = settings.internal_api_key.get_secret_value().encode()
    if not hmac.compare_digest((x_internal_key or "").encode(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "내부 API 키가 올바르지 않습니다.")


# 횟수를 프로세스 전체에서 함께 세야 해서 하나만 만듦
@lru_cache
def get_rate_limits() -> RateLimits:
    return RateLimits(get_settings())


# 공개 API 전체에 거는 IP 제한. main.py에서 공개 라우터를 등록할 때 붙임
# 횟수를 세는 dict를 여러 스레드가 동시에 바꾸지 않게 async로 둬 이벤트 루프에서만 실행함
async def limit_public_requests(
    request: Request, limits: RateLimits = Depends(get_rate_limits)
) -> None:
    retry_after = limits.api_per_ip.hit(limits.client_ip(request))
    if retry_after is not None:
        raise RateLimitedError(retry_after)


# 채팅은 LLM을 불러 IP와 브라우저(X-Client-Id) 둘 다 더 낮게 제한함
async def limit_chat_requests(
    request: Request, client_id: ClientId, limits: RateLimits = Depends(get_rate_limits)
) -> None:
    retry_after = limits.chat_per_ip.hit(limits.client_ip(request))
    if retry_after is None:
        retry_after = limits.chat_per_client.hit(client_id)
    if retry_after is not None:
        raise RateLimitedError(retry_after)
