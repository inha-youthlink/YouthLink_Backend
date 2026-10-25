import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.dependencies import get_rate_limits
from app.core.rate_limit import RateLimits
from app.main import app
from app.repositories.chat_repository import ChatRepository

SQL_DIR = Path(__file__).parent / "sql"
TEST_POLICY_NOS = ("TEST-POLICY-01", "TEST-POLICY-02", "TEST-POLICY-03")


# 횟수는 프로세스 전체에서 공유돼 테스트끼리 합쳐 세지 않게 테스트마다 제한 없는 새 인스턴스를 씀
@pytest.fixture(autouse=True)
def no_rate_limits() -> Iterator[None]:
    unlimited = get_settings().model_copy(update={
        "rate_limit_per_minute": 0,
        "chat_rate_limit_per_minute": 0,
        "chat_client_rate_limit_per_minute": 0,
        "llm_daily_limit": 0,
    })
    app.dependency_overrides[get_rate_limits] = lambda: RateLimits(unlimited)
    yield
    app.dependency_overrides.pop(get_rate_limits, None)


class DatabaseTestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    test_database_url: str | None = None


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    url = DatabaseTestSettings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL이 없어 DB 테스트를 건너뜀")

    # 설정 실수로 개발·운영 DB를 지우지 않도록 테스트 전용 이름만 허용함
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"TEST_DATABASE_URL의 DB 이름은 _test로 끝나야 함: {database}")

    try:
        asyncio.run(_create_database(url))
    except (OSError, asyncpg.PostgresError) as error:
        pytest.skip(f"테스트 DB를 만들 수 없어 DB 테스트를 건너뜀: {error}")

    yield url
    asyncio.run(_drop_database(url))


# 테스트마다 엔진을 새로 만들어 테스트별 이벤트 루프와 연결이 섞이지 않게 함
@pytest.fixture
async def session_factory(test_database_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(test_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    # 테스트끼리 영향을 주지 않도록 채팅 데이터만 비우고 테스트 정책은 남김
    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM chat_room"))
    await engine.dispose()


@pytest.fixture
def chat_repository(session_factory: async_sessionmaker) -> ChatRepository:
    return ChatRepository(session_factory)


def _asyncpg_dsn(url: str, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


async def _create_database(url: str) -> None:
    database = make_url(url).database
    admin = await asyncpg.connect(_asyncpg_dsn(url, "postgres"))
    try:
        # 이전 실행이 중간에 멈춰 남은 테스트 DB가 있어도 새로 시작함
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{database}"')
    finally:
        await admin.close()

    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        await conn.execute((SQL_DIR / "extensions.sql").read_text(encoding="utf-8"))
        await conn.execute((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
        await conn.execute((SQL_DIR / "region.sql").read_text(encoding="utf-8"))
        await conn.executemany(
            "INSERT INTO policy (policy_no, policy_name) VALUES ($1, $2)",
            [(policy_no, f"테스트 정책 {policy_no[-2:]}") for policy_no in TEST_POLICY_NOS],
        )
    finally:
        await conn.close()


async def _drop_database(url: str) -> None:
    admin = await asyncpg.connect(_asyncpg_dsn(url, "postgres"))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{make_url(url).database}" WITH (FORCE)')
    finally:
        await admin.close()
