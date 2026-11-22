from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.dependencies import PipelineDeps
from app.llm.client import ToolCall, ToolLoopResult
from app.pipeline.agent.recommend import (
    AgentCardOutput,
    AgentOutputError,
    AgentTurnOutput,
    FALLBACK_REASON,
    AgentTurnResult,
    agent_input,
    run_agent_turn,
    validate,
)
from app.pipeline.agent.tools import SeenPolicies
from app.pipeline.conditions import SearchConditions
from app.repositories.agent_repository import AgentCardRef, AgentMessageRecord
from app.schemas.policy_info import ApplicationStatus
from app.schemas.session import PolicySummary

CONDITIONS = SearchConditions(lines=["지역: 서울특별시 강남구", "나이: 27세"])
NOW = datetime(2026, 11, 22, 12, 0)


def _output(cards=(), question="어떤 도움이 필요하세요?", options=("월세", "전세"), message="찾았어요"):
    return AgentTurnOutput(
        message=message, question=question, options=list(options),
        cards=[AgentCardOutput(policy=policy, reason=reason) for policy, reason in cards],
    )


def _summary(no: str, title: str) -> PolicySummary:
    return PolicySummary(id=no, title=title, category=None, apply_start=None, apply_end=None,
                         status=ApplicationStatus.OPEN, region_label=None, organization=None, source_url=None)


# 본 적 없는 번호·중복은 버리고 최대 5개
def test_validate_cards():
    seen = SeenPolicies([f"N{i}" for i in range(1, 8)])
    cards = [("P1", " 이유 "), ("P99", "지어냄"), ("P1", "중복"), ("P2", " ")] + [(f"P{i}", "x") for i in range(3, 8)]
    _, refs = validate(_output(cards), seen)
    # 빈 이유는 null이 아니라 기본 문구 (null은 시작 카드 표시)
    assert refs[:2] == [AgentCardRef("N1", "이유"), AgentCardRef("N2", FALLBACK_REASON)]
    assert [ref.policy_no for ref in refs] == ["N1", "N2", "N3", "N4", "N5"]


def test_validate_question_and_options():
    answer, _ = validate(_output(options=[" 월세 ", "", "월세", "a", "b", "c", "d", "e"]), SeenPolicies())
    assert answer.options == ["월세", "a", "b", "c", "d"]
    answer, _ = validate(_output(question="  ", options=["월세"], message=" "), SeenPolicies())
    assert (answer.question, answer.options) == (None, [])
    assert answer.content == "말씀하신 내용으로 정책을 찾아봤어요."


def test_agent_input():
    seen = SeenPolicies(["N1"])
    history = [
        AgentMessageRecord(1, "user", "주거", None, [], NOW),
        AgentMessageRecord(2, "assistant", "찾았어요", "어떤 주거요?", ["월세", "전세"], NOW,
                           carried={"seen_policies": ["N1"], "cards": ["N1"]}),
    ]
    shown = [(seen.label("N2"), _summary("N2", "청년 월세 지원"))]
    text = agent_input(CONDITIONS, history, shown, "월세", seen)
    assert text.splitlines() == [
        "[사용자 프로필]", "지역: 서울특별시 강남구", "나이: 27세", "",
        "[지금 보이는 카드]", "P2 청년 월세 지원 (신청 가능)", "",
        "[최근 대화] (오래된 순)",
        "도우미: (시작) / 질문: 어떤 도움이 필요하세요? (선택지: 일자리, 주거, 교육, 복지·문화, 참여·권리)",
        "사용자: 주거",
        "도우미: 찾았어요 / 질문: 어떤 주거요? (선택지: 월세, 전세) / 카드: P1",
        "", "[사용자 답]", "월세",
    ]


# 대화가 잘렸으면 시작 질문은 넣지 않음
def test_agent_input_without_start_when_truncated():
    history = [AgentMessageRecord(i, "user", "x", None, [], NOW) for i in range(12)]
    assert "(시작)" not in agent_input(CONDITIONS, history, [], "y", SeenPolicies())


class FakeLLM:
    chat_model = "chat-model"

    def __init__(self, parsed, calls=()) -> None:
        self._parsed = parsed
        self._calls = calls
        self.messages: list = []
        self.options: dict = {}

    async def run_tools(self, messages, tools, execute, text_format, *, max_calls, timeout):
        self.messages = messages
        self.options = {"max_calls": max_calls, "timeout": timeout}
        for call in self._calls:
            yield call
        yield ToolLoopResult(self._parsed, 100, 20, 2, list(self._calls))


async def _turn(llm, history=(), shown=()):
    deps = PipelineDeps(SimpleNamespace(agent_timeout=30, top_k=30), llm, None, None, None)
    return [event async for event in run_agent_turn(deps, CONDITIONS, list(history), list(shown), "월세")]


# 지금 카드(시작 카드)에도 번호를 붙여 이어 씀. 본 정책 목록과 카드는 metadata에 남겨 다음 턴에 씀
async def test_run_agent_turn_metadata():
    history = [AgentMessageRecord(2, "assistant", "x", None, [], NOW, carried={"seen_policies": ["N1"]})]
    call = ToolCall("search_policies", {"category": "주거"})
    llm = FakeLLM(_output([("P2", "맞아요")]), [call])
    events = await _turn(llm, history, [_summary("N2", "청년 월세 지원")])
    assert events[0] == call
    result = events[1]
    assert isinstance(result, AgentTurnResult)
    assert result.cards == [AgentCardRef("N2", "맞아요")]
    meta = result.answer.metadata
    assert meta["seen_policies"] == ["N1", "N2"]
    assert meta["cards"] == ["N2"]
    assert meta["prompt_version"] == "agent_recommend_v1"
    assert meta["tokens"] == {"prompt": 100, "completion": 20}
    assert llm.options == {"max_calls": 3, "timeout": 30}
    assert llm.messages[0]["role"] == "system"


async def test_run_agent_turn_without_cards():
    events = await _turn(FakeLLM(_output()))
    assert events[-1].cards is None
    assert events[-1].answer.metadata["cards"] == []


async def test_run_agent_turn_refusal():
    with pytest.raises(AgentOutputError):
        await _turn(FakeLLM(None))
