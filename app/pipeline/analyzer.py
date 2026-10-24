import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from openai import OpenAIError
from pydantic import ValidationError

from app.pipeline.context import PipelineContext
from app.pipeline.prompts import load_prompt
from app.schemas.analysis import Intent, JobStatus, QueryAnalysis, SchoolLevel, StatusFilter, SubQuery
from app.schemas.pipeline import ConversationTurn, PipelineInput, PolicyRef

logger = logging.getLogger(__name__)

PROMPT_NAME = "analyze_v3"
# 프롬프트 규칙("최대 3개")과 함께 바뀌어야 해서 설정값이 아닌 상수로 둠
MAX_SUB_QUERIES = 3
NONE_TEXT = "없음"


class FallbackReason(StrEnum):
    LLM_ERROR = "llm_error"
    SCHEMA_ERROR = "schema_error"


@dataclass(frozen=True)
class ResolvedSubQuery:
    standalone_question: str
    # 검증 전 값. target_policy_nos와 비교해 지목 오류를 찾음
    target_refs: list[str]
    # 현재 정책 갱신이 후보를 다시 찾지 않도록 이름까지 담음
    targets: list[PolicyRef]
    intent: Intent
    status_filter: StatusFilter
    search_query: str = ""
    region: str = ""
    age: int | None = None
    job_status: JobStatus = JobStatus.NONE
    school: SchoolLevel = SchoolLevel.NONE
    # 후보에 없는 표기. 없는 카드 번호를 가리킨 경우라 라우터가 되묻기로 보냄
    unresolved_refs: list[str] = field(default_factory=list)

    @property
    def target_policy_nos(self) -> list[str]:
        return [policy.policy_no for policy in self.targets]


@dataclass(frozen=True)
class AnalysisResult:
    sub_queries: list[ResolvedSubQuery]
    is_new_topic: bool
    fallback_reason: FallbackReason | None
    prompt_version: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_trace(self) -> dict[str, Any]:
        return {
            "prompt_version": self.prompt_version,
            "model": self.model,
            "tokens": {"prompt": self.prompt_tokens, "completion": self.completion_tokens},
            "sub_queries": [
                {
                    "standalone_question": sub.standalone_question,
                    "search_query": sub.search_query,
                    "target_refs": sub.target_refs,
                    "target_policy_nos": sub.target_policy_nos,
                    "intent": sub.intent.value,
                    "status_filter": sub.status_filter.value,
                    "region": sub.region,
                    "age": sub.age,
                    "job_status": sub.job_status.value,
                    "school": sub.school.value,
                }
                for sub in self.sub_queries
            ],
            "is_new_topic": self.is_new_topic,
            "fallback_reason": self.fallback_reason.value if self.fallback_reason else None,
        }

    # 하위 질문이 여럿이면 원래 질문 대신 맥락이 풀린 질문을 이어 붙여 지시어·배경 설명을 피함
    def standalone_question(self, original: str) -> str:
        if not self.sub_queries:
            return original
        return " ".join(sub.standalone_question for sub in self.sub_queries)

    # 조건을 뺀 검색어. 인사처럼 검색어가 비면 독립 질문으로 검색함
    def search_query(self, original: str) -> str:
        queries = [sub.search_query.strip() for sub in self.sub_queries if sub.search_query.strip()]
        return " ".join(queries) if queries else self.standalone_question(original)


# 분석은 정확도를 높이는 단계라 실패해도 예외를 넘기지 않고 원래 질문으로 진행함
async def analyze(ctx: PipelineContext) -> AnalysisResult:
    llm = ctx.deps.llm
    labels, user_message = _build_input(ctx.input, ctx.deps.settings.analysis_answer_max_chars)
    system_prompt = load_prompt(PROMPT_NAME)

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


def _resolve(sub: SubQuery, labels: dict[str, PolicyRef]) -> ResolvedSubQuery:
    refs = [ref.strip().upper() for ref in sub.target_refs if ref.strip()]
    targets = {labels[ref].policy_no: labels[ref] for ref in refs if ref in labels}
    return ResolvedSubQuery(
        standalone_question=sub.standalone_question,
        search_query=sub.search_query,
        target_refs=sub.target_refs,
        targets=list(targets.values()),
        intent=sub.intent,
        status_filter=sub.status_filter,
        region=sub.region.strip(),
        # 나이를 잘못 읽은 값으로 정책을 빼지 않도록 사람 나이로 보기 어려운 값은 버림
        age=sub.age if sub.age is not None and 0 < sub.age < 100 else None,
        job_status=sub.job_status,
        school=sub.school,
        unresolved_refs=[ref for ref in refs if ref not in labels],
    )


# 숫자는 화면 카드 번호와 같아야 해서 직전 답변에만 붙이고, 화면 밖 현재 정책은 C 표기로 구분함
def _build_input(inp: PipelineInput, answer_max_chars: int) -> tuple[dict[str, PolicyRef], str]:
    shown = inp.history[-1].policies if inp.history else []
    labels = {str(order): policy for order, policy in enumerate(shown, start=1)}
    shown_label = {policy.policy_no: label for label, policy in labels.items()}
    shown_lines = [f"{order}. {policy.policy_name}" for order, policy in enumerate(shown, start=1)]

    current_lines = []
    off_screen = 0
    for policy in inp.current_policies:
        if policy.policy_no in shown_label:
            current_lines.append(f"{shown_label[policy.policy_no]}번과 같음")
            continue
        off_screen += 1
        label = f"C{off_screen}"
        labels[label] = policy
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
