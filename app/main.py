from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import health, internal, sessions
from app.core.config import get_settings
from app.core.dependencies import limit_public_requests, verify_origin
from app.core.errors import register_error_handlers
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.llm.client import LLMClient
from app.pipeline.retrieval.factory import create_retriever
from app.repositories.chat_repository import ChatRepository
from app.repositories.common_code_repository import CommonCodeRepository
from app.repositories.policy_repository import PolicyRepository
from app.repositories.vector_repository import VectorRepository


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    app.state.llm = LLMClient.from_settings(settings)
    app.state.engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    app.state.session_factory = async_sessionmaker(app.state.engine, expire_on_commit=False)

    # 잘못된 RETRIEVER 값이 첫 요청이 아니라 서버 시작 시 실패하도록 여기서 생성함
    app.state.policy_repository = PolicyRepository(app.state.session_factory)
    app.state.common_code_repository = CommonCodeRepository(app.state.session_factory)
    app.state.chat_repository = ChatRepository(app.state.session_factory)
    app.state.retriever = create_retriever(
        settings.retriever,
        app.state.llm,
        VectorRepository(app.state.session_factory),
    )

    yield

    await app.state.llm.close()
    await app.state.engine.dispose()


settings = get_settings()

# 운영에서는 API 문서를 끄고 경로 목록을 공개하지 않음
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
    docs_url=None if settings.is_prod else "/docs",
    redoc_url=None if settings.is_prod else "/redoc",
    openapi_url=None if settings.is_prod else "/openapi.json",
)

register_error_handlers(app)
# 나중에 붙인 미들웨어가 바깥쪽이라, 본문 크기 거절(413) 응답에도 보안 헤더가 붙음
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(health.router)
app.include_router(internal.router)
# 공개 라우터는 여기서 출처 확인과 IP 요청 제한을 함께 붙임. /internal(평가 서버)과 /health(헬스 체크)는 제외
PUBLIC_DEPENDENCIES = [Depends(verify_origin), Depends(limit_public_requests)]
app.include_router(sessions.router, dependencies=PUBLIC_DEPENDENCIES)
