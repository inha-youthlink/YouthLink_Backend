from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from app.core.config import Settings, get_settings
from app.core.errors import InvalidClientIdError
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
