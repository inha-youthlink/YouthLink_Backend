import logging
from dataclasses import dataclass
from enum import StrEnum

from openai import OpenAIError
from pydantic import ValidationError

from app.pipeline.context import PipelineContext
from app.pipeline.generator import PROMPTS_DIR
from app.schemas.analysis import Intent, QueryAnalysis, StatusFilter, SubQuery
from app.schemas.pipeline import ConversationTurn, PipelineInput

logger = logging.getLogger(__name__)

PROMPT_NAME = "analyze_v1"
# 프롬프트 규칙("최대 3개")과 함께 바뀌어야 해서 설정값이 아닌 상수로 둠
MAX_SUB_QUERIES = 3
NONE_TEXT = "없음"


class FallbackReason(StrEnum):
    LLM_ERROR = "llm_error"
    SCHEMA_ERROR = "schema_error"


@dataclass(frozen=True)
class ResolvedSubQuery:
    standalone_question: str
    # 검증 전 LLM 출력을 남겨 target_policy_nos와 다르면 지목 오류로 볼 수 있음
    target_refs: list[str]
    target_policy_nos: list[str]
    intent: Intent
    status_filter: StatusFilter


@dataclass(frozen=True)
class AnalysisResult:
    sub_queries: list[ResolvedSubQuery]
    is_new_topic: bool
    fallback_reason: FallbackReason | None
    prompt_version: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # 하위 질문이 여럿이면 원래 질문 대신 맥락이 풀린 질문을 이어 붙여 지시어·배경 설명을 피함
    def search_query(self, original: str) -> str:
        if not self.sub_queries:
            return original
        return " ".join(sub.standalone_question for sub in self.sub_queries)


# 분석은 정확도를 높이는 단계라 실패해도 예외를 넘기지 않고 원래 질문으로 진행함
async def analyze(ctx: PipelineContext) -> AnalysisResult:
    llm = ctx.deps.llm
    labels, user_message = _build_input(ctx.input, ctx.deps.settings.analysis_answer_max_chars)
    system_prompt = (PROMPTS_DIR / f"{PROMPT_NAME}.md").read_text(encoding="utf-8")

    try:
        result = await llm.analyze(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            QueryAnalysis,
        )
    except OpenAIError:
        logger.warning("질문 분석 호출 실패, 원래 질문으로 진행", exc_info=True)
        return _failed(FallbackReason.LLM_ERROR, llm.analysis_model)
    except ValidationError:
        logger.warning("질문 분석 출력 형식 오류, 원래 질문으로 진행", exc_info=True)
        return _failed(FallbackReason.SCHEMA_ERROR, llm.analysis_model)

    parsed = result.parsed
    if parsed is None or not parsed.sub_queries:
        logger.warning("질문 분석 결과 없음, 원래 질문으로 진행")
        return _failed(
            FallbackReason.SCHEMA_ERROR, llm.analysis_model, result.prompt_tokens, result.completion_tokens
        )

    return AnalysisResult(
        sub_queries=[_resolve(sub, labels) for sub in parsed.sub_queries[:MAX_SUB_QUERIES]],
        is_new_topic=parsed.is_new_topic,
        fallback_reason=None,
        prompt_version=PROMPT_NAME,
        model=llm.analysis_model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )


def _failed(
    reason: FallbackReason, model: str, prompt_tokens: int = 0, completion_tokens: int = 0
) -> AnalysisResult:
    # 화제 전환으로 보면 현재 정책이 초기화되므로 실패 시에는 대화 상태를 유지함
    return AnalysisResult(
        sub_queries=[],
        is_new_topic=False,
        fallback_reason=reason,
        prompt_version=PROMPT_NAME,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _resolve(sub: SubQuery, labels: dict[str, str]) -> ResolvedSubQuery:
    refs = (ref.strip().upper() for ref in sub.target_refs)
    policy_nos = [labels[ref] for ref in refs if ref in labels]
    return ResolvedSubQuery(
        standalone_question=sub.standalone_question,
        target_refs=sub.target_refs,
        target_policy_nos=list(dict.fromkeys(policy_nos)),
        intent=sub.intent,
        status_filter=sub.status_filter,
    )


# 숫자는 화면 카드 번호와 같아야 해서 직전 답변에만 붙이고, 화면 밖 현재 정책은 C 표기로 구분함
def _build_input(inp: PipelineInput, answer_max_chars: int) -> tuple[dict[str, str], str]:
    shown = inp.history[-1].policies if inp.history else []
    labels = {str(order): policy.policy_no for order, policy in enumerate(shown, start=1)}
    shown_label = {policy_no: label for label, policy_no in labels.items()}
    shown_lines = [f"{order}. {policy.policy_name}" for order, policy in enumerate(shown, start=1)]

    current_lines = []
    off_screen = 0
    for policy in inp.current_policies:
        if policy.policy_no in shown_label:
            current_lines.append(f"{shown_label[policy.policy_no]}번과 같음")
            continue
        off_screen += 1
        label = f"C{off_screen}"
        labels[label] = policy.policy_no
        current_lines.append(f"{label}. {policy.policy_name}")

    history_lines = [
        _format_turn(order, turn, answer_max_chars) for order, turn in enumerate(inp.history, start=1)
    ]
    user_message = "\n".join([
        "[최근 대화]",
        "\n".join(history_lines) or NONE_TEXT,
        "[직전에 보여준 정책]",
        "\n".join(shown_lines) or NONE_TEXT,
        "[현재 대화 중인 정책]",
        "\n".join(current_lines) or NONE_TEXT,
        "[질문]",
        inp.question,
    ])
    return labels, user_message


def _format_turn(order: int, turn: ConversationTurn, answer_max_chars: int) -> str:
    policies = ", ".join(policy.policy_name for policy in turn.policies) or NONE_TEXT
    return "\n".join([
        f"<턴 {order}>",
        f"질문: {_one_line(turn.question)}",
        f"답변: {_truncate(_one_line(turn.answer), answer_max_chars)}",
        f"보여준 정책: {policies}",
    ])


# 답변의 줄바꿈 목록이 <턴> 구분과 섞이지 않게 한 줄로 합침
def _one_line(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"
