import asyncio
import uuid
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, OpenAIError

from app.api.sessions import get_assist_service
from app.core.config import get_settings
from app.core.dependencies import get_rate_limits
from app.core.rate_limit import RateLimits
from app.llm.client import ChatResult
from app.main import app
from app.pipeline.conditions import RegionIndex, build_conditions
from app.schemas.pipeline import ConversationTurn, PolicyRef, Profile
from app.services import assist_service
from app.services.assist_service import AssistService, summary_input
from tests.conftest import (
    CLIENT_ID,
    HEADERS,
    OTHER_CLIENT_ID,
    REGIONS,
    FakeChatRepository,
    FakePolicyRepository,
)

PROFILE = Profile(age=27, region_code="11680", job_code="0013003")


class FakeCodes:
    async def get_names(self, codes):
        return {code: "미취업자" for code in codes}


class FakeLLM:
    def __init__(self, text="요약 문장입니다.", error: BaseException | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    async def chat(self, messages, **options):
        self.calls.append({"messages": messages, **options})
        if self.error:
            raise self.error
        return ChatResult(text=self.text, prompt_tokens=10, completion_tokens=5)


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM(text="  서울 강남구 27세 청년이 월세 지원을 물었습니다.\n")


@pytest.fixture
def client(repo, llm):
    deps = SimpleNamespace(
        policy_repository=FakePolicyRepository(), common_code_repository=FakeCodes(), llm=llm, settings=get_settings()
    )
    app.dependency_overrides[get_assist_service] = lambda: AssistService(repo, deps)
    yield TestClient(app)
    app.dependency_overrides.pop(get_assist_service, None)


@pytest.fixture
def room_id(repo) -> uuid.UUID:
    return asyncio.run(repo.create_room(CLIENT_ID, PROFILE))


def _turn(repo, room_id, question, answer, policies=()):
    async def save():
        question_id = await repo.save_question(room_id, uuid.uuid4(), question, question)
        await repo.save_answer(
            room_id=room_id, question_id=question_id, answer=answer, answer_metadata={},
            policies=[], current_policies=[],
        )
    asyncio.run(save())


def _summary(client, room_id, headers=HEADERS):
    return client.post(f"/api/sessions/{room_id}/summary", headers=headers)


def test_summary(client, repo, llm, room_id):
    _turn(repo, room_id, "월세 지원 알려줘", "서울 청년 월세 지원이 있어요.")

    response = _summary(client, room_id)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    # 앞뒤 공백을 지움
    assert response.json() == {"summary": "서울 강남구 27세 청년이 월세 지원을 물었습니다."}
    call = llm.calls[0]
    # 짧게 끝나도록 출력 상한·낮은 추론, 시간 제한을 여러 번 기다리지 않게 재시도 없음
    assert (call["max_output_tokens"], call["reasoning_effort"], call["max_retries"]) == (1000, "low", 0)
    assert call["messages"][0]["role"] == "system"
    assert "월세 지원 알려줘" in call["messages"][1]["content"]


# 대화가 없으면 LLM을 부르지 않고 하루 상한에도 세지 않음
def test_summary_without_conversation(client, llm, room_id):
    limits = RateLimits(get_settings().model_copy(update={"llm_daily_limit": 1}))
    app.dependency_overrides[get_rate_limits] = lambda: limits

    response = _summary(client, room_id)

    assert response.json() == {"summary": "아직 나눈 대화가 없습니다."}
    assert llm.calls == []
    assert limits.llm_daily.hit() is None


def test_summary_counts_daily_llm_limit(client, repo, room_id):
    _turn(repo, room_id, "질문", "답변")
    limits = RateLimits(get_settings().model_copy(update={"llm_daily_limit": 1}))
    app.dependency_overrides[get_rate_limits] = lambda: limits

    first = _summary(client, room_id)
    second = _summary(client, room_id)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"


def test_summary_uses_chat_rate_limit(client, repo, room_id):
    _turn(repo, room_id, "질문", "답변")
    limits = RateLimits(get_settings().model_copy(update={"chat_client_rate_limit_per_minute": 1}))
    app.dependency_overrides[get_rate_limits] = lambda: limits

    assert _summary(client, room_id).status_code == 200
    assert _summary(client, room_id).status_code == 429


@pytest.mark.parametrize(("error", "status", "code"), [
    (APITimeoutError(request=httpx2.Request("POST", "https://llm.test")), 504, "RAG_TIMEOUT"),
    (OpenAIError("gateway down"), 502, "RAG_UPSTREAM_ERROR"),
])
def test_summary_llm_errors(client, repo, llm, room_id, error, status, code):
    _turn(repo, room_id, "질문", "답변")
    llm.error = error

    response = _summary(client, room_id)

    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    # 요약 전용 문구, 내부 오류 문구는 나가지 않음
    assert body["message"].startswith("요약을 만드")
    assert "gateway" not in body["message"]


# 대화가 있는데 출력이 비면 "대화 없음" 문구가 아니라 생성 실패
@pytest.mark.parametrize("text", ["", "  \n "])
def test_summary_empty_output_is_upstream_error(client, repo, llm, room_id, text):
    _turn(repo, room_id, "질문", "답변")
    llm.text = text

    response = _summary(client, room_id)

    assert response.status_code == 502
    assert response.json() == {"code": "RAG_UPSTREAM_ERROR", "message": "요약을 만드는 중 문제가 생겼어요. 다시 시도해 주세요."}


def test_summary_other_users_session(client, llm, room_id):
    response = _summary(client, room_id, headers={"X-Client-Id": OTHER_CLIENT_ID})

    assert response.status_code == 404
    assert response.json()["code"] == "SESSION_NOT_FOUND"
    assert llm.calls == []


def test_summary_input_cuts_long_answers_and_lists_policies():
    conditions = build_conditions(None, PROFILE, RegionIndex(REGIONS), {"0013003": "미취업자"})
    turns = [
        ConversationTurn(question=" 월세 지원 알려줘 ", answer="가" * 1200,
                         policies=[PolicyRef(policy_no="P-1", policy_name="청년 월세 지원"),
                                   PolicyRef(policy_no="P-2", policy_name="월세 대출")]),
        ConversationTurn(question="신청 방법은?", answer="온라인으로 신청해요."),
    ]

    text = summary_input(conditions, turns)

    assert text.splitlines()[:4] == ["[사용자 조건]", "지역: 서울특별시 강남구", "나이: 27세", "취업 상태: 미취업자"]
    assert "1. 질문: 월세 지원 알려줘" in text
    assert f"   답변 앞부분: {'가' * assist_service.SUMMARY_ANSWER_CHARS}…(이하 생략)" in text
    assert "   안내한 정책: 청년 월세 지원, 월세 대출" in text
    assert "2. 질문: 신청 방법은?\n   답변 앞부분: 온라인으로 신청해요." in text
