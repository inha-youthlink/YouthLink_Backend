from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from time import perf_counter

from openai import OpenAIError
from pydantic import BaseModel

from app.core.dependencies import PipelineDeps
from app.llm.client import ToolCall, ToolLoopResult
from app.pipeline.agent.tools import AGENT_CATEGORIES, AGENT_TOOLS, AgentTools, SeenPolicies
from app.pipeline.conditions import SearchConditions
from app.pipeline.generator import STATUS_LABELS
from app.pipeline.prompts import load_prompt
from app.repositories.agent_repository import AgentAnswer, AgentCardRef, AgentMessageRecord
from app.schemas.policy_info import today_kst
from app.schemas.session import PolicySummary

# 추천 패널 한 턴 (계약 4.8): 지시문·입력 조립 → 도구 루프 → 출력 검증. DB에는 쓰지 않음 (저장은 서비스)

PROMPT_VERSION = "agent_recommend_v1"
# 첫 질문은 분야로 고정 (구상 문서 결정). 칩 글자 그대로 사용자 답으로 돌아옴
START_QUESTION = "어떤 도움이 필요하세요?"
START_OPTIONS = list(AGENT_CATEGORIES)
# 최근 6턴 (사용자·답변 12개). PR 4 평가에서 조정
HISTORY_MESSAGES = 12
# 한 턴에 도구 3번이면 보통 턴(검색 1~2번 + 조회 1번)이 들어감. 넘으면 도구를 끄고 답을 받음
MAX_TOOL_CALLS = 3
MAX_CARDS = 5
MAX_OPTIONS = 5
FALLBACK_CONTENT = "말씀하신 내용으로 정책을 찾아봤어요."
# 프론트는 reason이 null이면 시작 카드(프로필 기준)로 보므로(계약 3.5) 에이전트가 고른 카드는 비우지 않음
FALLBACK_REASON = "지금까지 말씀하신 내용에 맞춰 고른 정책이에요."


class AgentCardOutput(BaseModel):
    policy: str
    reason: str


class AgentTurnOutput(BaseModel):
    message: str
    question: str | None
    options: list[str]
    cards: list[AgentCardOutput]


# 모델이 답을 거부하거나 형식이 깨지면 다른 LLM 실패와 같이 처리되게 OpenAIError로 둠
class AgentOutputError(OpenAIError):
    def __init__(self) -> None:
        super().__init__("추천 에이전트 출력이 비어 있음 (거부 또는 형식 오류)")


# 저장할 답변과 카드. cards가 None이면 카드를 바꾸지 않은 답변
@dataclass(frozen=True)
class AgentTurnResult:
    answer: AgentAnswer
    cards: list[AgentCardRef] | None


# 도구를 실행하기 직전마다 ToolCall을, 마지막에 AgentTurnResult를 내보냄
# 시간 제한은 부르는 쪽이 검 (생성기 안에서 걸면 내보내는 사이의 기다림까지 셈)
async def run_agent_turn(
    deps: PipelineDeps,
    conditions: SearchConditions,
    history: list[AgentMessageRecord],
    shown_cards: list[PolicySummary],
    content: str,
) -> AsyncGenerator[ToolCall | AgentTurnResult, None]:
    started = perf_counter()
    seen = SeenPolicies(_last_seen(history))
    # 지금 보이는 카드(시작 카드 포함)도 번호를 붙여 "첫 번째 카드 자세히"나 카드 유지에 쓸 수 있게 함
    shown = [(seen.label(card.id), card) for card in shown_cards]
    tools = AgentTools(deps, conditions, seen, today_kst())
    messages = [
        {"role": "system", "content": load_prompt(PROMPT_VERSION)},
        {"role": "user", "content": agent_input(conditions, history, shown, content, seen)},
    ]

    result: ToolLoopResult[AgentTurnOutput] | None = None
    events = deps.llm.run_tools(
        messages, AGENT_TOOLS, tools.execute, AgentTurnOutput,
        max_calls=MAX_TOOL_CALLS, timeout=deps.settings.agent_timeout,
    )
    async with aclosing(events) as stream:
        async for event in stream:
            if isinstance(event, ToolCall):
                yield event
            else:
                result = event
    if result is None or result.parsed is None:
        raise AgentOutputError

    answer, cards = validate(result.parsed, seen)
    metadata = {
        "prompt_version": PROMPT_VERSION,
        "model": deps.llm.chat_model,
        "seen_policies": seen.policy_nos,
        "cards": [card.policy_no for card in cards],
        "tool_calls": [record.to_trace() for record in tools.records],
        "rounds": result.rounds,
        "tokens": {"prompt": result.prompt_tokens, "completion": result.completion_tokens},
        "latency_ms": {"total": int((perf_counter() - started) * 1000)},
        "raw_output": result.parsed.model_dump(),
    }
    yield AgentTurnResult(
        answer=AgentAnswer(content=answer.content, question=answer.question, options=answer.options, metadata=metadata),
        cards=cards or None,
    )


# 서버 검증: 본 정책만, 중복 없이 최대 5개 (계약 4.8). 질문이 없으면 선택지도 없음
def validate(output: AgentTurnOutput, seen: SeenPolicies) -> tuple[AgentAnswer, list[AgentCardRef]]:
    cards: list[AgentCardRef] = []
    for card in output.cards:
        policy_no = seen.resolve(card.policy)
        if policy_no is None or any(existing.policy_no == policy_no for existing in cards):
            continue
        cards.append(AgentCardRef(policy_no=policy_no, reason=card.reason.strip() or FALLBACK_REASON))
        if len(cards) == MAX_CARDS:
            break

    question = (output.question or "").strip() or None
    options: list[str] = []
    for option in output.options if question else []:
        option = option.strip()
        if option and option not in options:
            options.append(option)
    content = output.message.strip() or FALLBACK_CONTENT
    return AgentAnswer(content=content, question=question, options=options[:MAX_OPTIONS]), cards


def agent_input(
    conditions: SearchConditions,
    history: list[AgentMessageRecord],
    shown: list[tuple[str, PolicySummary]],
    content: str,
    seen: SeenPolicies,
) -> str:
    lines = ["[사용자 프로필]", *(conditions.lines or ["정보 없음"]), "", "[지금 보이는 카드]"]
    lines += [f"{label} {card.title} ({STATUS_LABELS[card.status] if card.status else '정보 없음'})"
              for label, card in shown] or ["없음"]
    lines += ["", "[최근 대화] (오래된 순)"]
    # 대화 전체가 들어갈 때만 시작 질문을 붙임 (잘렸으면 이미 지난 내용)
    if len(history) < HISTORY_MESSAGES:
        lines.append(f"도우미: (시작) / 질문: {START_QUESTION} (선택지: {', '.join(START_OPTIONS)})")
    lines += [_history_line(record, seen) for record in history]
    lines += ["", "[사용자 답]", content]
    return "\n".join(lines)


def _history_line(record: AgentMessageRecord, seen: SeenPolicies) -> str:
    if record.role == "user":
        return f"사용자: {record.content}"
    parts = [f"도우미: {record.content}"]
    if record.question:
        options = f" (선택지: {', '.join(record.options)})" if record.options else ""
        parts.append(f"질문: {record.question}{options}")
    cards = record.carried.get("cards") or []
    if cards:
        parts.append("카드: " + ", ".join(seen.label(policy_no) for policy_no in cards))
    return " / ".join(parts)


# 번호는 가장 최근 답변이 저장한 목록을 이어 씀 (그 답변까지 본 정책이 모두 들어 있음)
def _last_seen(history: list[AgentMessageRecord]) -> list[str]:
    for record in reversed(history):
        if record.role == "assistant":
            return list(record.carried.get("seen_policies") or [])
    return []
