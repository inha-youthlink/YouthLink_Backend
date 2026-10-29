import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.dependencies import get_rate_limits
from app.core.rate_limit import RateLimits
from app.core.session_lock import SessionLocks
from app.main import app
from app.pipeline.runner import AnswerToken, PipelineEvent, PolicyCards
from app.repositories.chat_repository import ChatRepository, QuestionRecord, RoomRecord
from app.repositories.policy_repository import RegionRow
from app.schemas.chat import MessageItem
from app.schemas.pipeline import (
    ConversationTurn,
    PipelineInput,
    PipelineOutput,
    PolicyItem,
    PolicyRef,
    Profile,
)
from app.services.chat_service import ChatService

SQL_DIR = Path(__file__).parent / "sql"
TEST_POLICY_NOS = ("TEST-POLICY-01", "TEST-POLICY-02", "TEST-POLICY-03")


CLIENT_ID = "6f1c2b9e-3d4a-4c8b-9f2e-1a2b3c4d5e6f"
OTHER_CLIENT_ID = "0b6a4b1e-8d1f-4f5e-a2c3-9e8d7c6b5a40"
HEADERS = {"X-Client-Id": CLIENT_ID}
CREATED_AT = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)

# 계약 형식 프로필 (8항목 필수)
PROFILE = {
    "region": "11680",
    "maritalStatus": "SINGLE",
    "age": 27,
    "annualIncome": 3000,
    "education": "COLLEGE_GRADUATED",
    "major": "ENGINEERING",
    "employmentStatus": "UNEMPLOYED",
    "specialField": "NONE",
}
REGIONS = [
    RegionRow("11", "서울특별시", "종로구", "11110"),
    RegionRow("11", "서울특별시", "강남구", "11680"),
    RegionRow("41", "경기도", "수원시 장안구", "41111"),
    RegionRow("41", "경기도", "수원시 권선구", "41113"),
]


class FakePolicyRepository:
    async def list_regions(self) -> list[RegionRow]:
        return REGIONS


# API 테스트용 메모리 저장소. 실제 SQL은 test_chat_repository가 테스트 DB로 확인함
class FakeChatRepository:
    def __init__(self) -> None:
        self.rooms: dict[uuid.UUID, RoomRecord] = {}
        self.messages: dict[uuid.UUID, list[MessageItem]] = {}
        self.refs: dict[int, list[PolicyItem]] = {}
        self.current: dict[uuid.UUID, list[PolicyRef]] = {}
        # 답변 id → 질문 id
        self.reply_to: dict[int, int] = {}
        self.metadata: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    async def create_room(self, user_id: str, profile: Profile) -> uuid.UUID:
        room_id = uuid.uuid4()
        created_at = CREATED_AT + timedelta(minutes=len(self.rooms))
        self.rooms[room_id] = RoomRecord(room_id, user_id, None, created_at, profile)
        return room_id

    async def get_room(self, room_id: uuid.UUID) -> RoomRecord | None:
        return self.rooms.get(room_id)

    async def list_rooms(self, user_id: str) -> list[RoomRecord]:
        rooms = [room for room in self.rooms.values() if room.user_id == user_id]
        return sorted(rooms, key=lambda room: room.created_at, reverse=True)

    async def list_messages(self, room_id: uuid.UUID) -> list[MessageItem]:
        return self.messages.get(room_id, [])

    async def list_policy_refs(self, message_ids: list[int]) -> dict[int, list[PolicyItem]]:
        return {message_id: self.refs[message_id] for message_id in message_ids if message_id in self.refs}

    async def list_recent_turns(self, room_id: uuid.UUID, turns: int) -> list[ConversationTurn]:
        if turns <= 0:
            return []
        by_id = {message.message_id: message for message in self.messages.get(room_id, [])}
        pairs = [
            (by_id[question_id], by_id[answer_id])
            for answer_id, question_id in self.reply_to.items()
            if answer_id in by_id
        ]
        return [ConversationTurn(question=question.content, answer=answer.content) for question, answer in pairs[-turns:]]

    async def list_current_policies(self, room_id: uuid.UUID) -> list[PolicyRef]:
        return self.current.get(room_id, [])

    async def find_question(self, room_id: uuid.UUID, client_message_id: uuid.UUID) -> QuestionRecord | None:
        messages = self.messages.get(room_id, [])
        question = next((m for m in messages if m.client_message_id == client_message_id), None)
        if question is None:
            return None
        answer = next(
            (m for m in messages if self.reply_to.get(m.message_id) == question.message_id), None
        )
        return QuestionRecord(
            question.message_id, question.content, answer.message_id if answer else None,
            answer.content if answer else None,
        )

    async def save_question(self, room_id: uuid.UUID, client_message_id: uuid.UUID, question: str, room_title: str) -> int:
        message_id = self._add(room_id, "user", question, client_message_id)
        room = self.rooms[room_id]
        if room.title is None:
            self.rooms[room_id] = RoomRecord(room.room_id, room.user_id, room_title, room.created_at, room.profile)
        return message_id

    async def save_answer(
        self, room_id, question_id, answer, answer_metadata, policies, current_policies
    ) -> int:
        answer_id = self._add(room_id, "assistant", answer, None)
        self.reply_to[answer_id] = question_id
        self.metadata[answer_id] = answer_metadata
        if policies:
            self.refs[answer_id] = list(policies)
        self.current[room_id] = list(current_policies)
        return answer_id

    def _add(self, room_id: uuid.UUID, role: str, content: str, client_message_id: uuid.UUID | None) -> int:
        message_id, self._next_id = self._next_id, self._next_id + 1
        self.messages.setdefault(room_id, []).append(MessageItem(
            message_id=message_id, role=role, content=content, client_message_id=client_message_id,
            created_at=CREATED_AT,
        ))
        return message_id


# 파이프라인 대신 정해진 카드·조각을 흘려보냄. 받은 입력은 calls에 남김
class FakePipeline:
    def __init__(self, tokens=("답변 ", "본문"), policies=(), fail: BaseException | None = None) -> None:
        self.tokens = list(tokens)
        self.policies = list(policies)
        self.fail = fail
        self.calls: list[PipelineInput] = []
        self.closed = False

    async def __call__(self, inp: PipelineInput, deps) -> AsyncIterator[PipelineEvent]:
        self.calls.append(inp)
        try:
            yield PolicyCards(self.policies)
            for token in self.tokens:
                yield AnswerToken(token)
            if self.fail is not None:
                raise self.fail
            yield PipelineOutput(
                answer="".join(self.tokens), policies=self.policies, trace={"chat_model": "fake"},
                current_policies=[PolicyRef(policy_no=p.policy_no, policy_name=p.policy_name) for p in self.policies],
            )
        finally:
            # 연결이 끊겨 중간에 닫혀도 여기까지 옴 (실제 파이프라인은 여기서 LLM 스트림이 닫힘)
            self.closed = True


def make_service(repo: FakeChatRepository, locks: SessionLocks | None = None) -> ChatService:
    deps = SimpleNamespace(policy_repository=FakePolicyRepository(), settings=get_settings())
    return ChatService(repo, deps, locks or SessionLocks())


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
