from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import health, internal, rooms
from app.core.config import get_settings
from app.core.errors import register_error_handlers
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

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

register_error_handlers(app)

app.include_router(health.router)
app.include_router(internal.router)
app.include_router(rooms.router)
