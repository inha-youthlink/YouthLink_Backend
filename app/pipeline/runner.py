import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

from app.core.dependencies import PipelineDeps
from app.pipeline import analyzer
from app.pipeline.cards import build_cards
from app.pipeline.conditions import build_conditions, load_code_names, load_regions
from app.pipeline.context import PipelineContext
from app.pipeline.current_policy import update_current_policies
from app.pipeline.evidence import Evidence
from app.pipeline.generator import (
    AnswerDelta,
    CardsDecided,
    GenerationResult,
    generate_stream,
)
from app.pipeline.handlers import clarify, detail, search, short
from app.pipeline.router import Handler, choose_route
from app.schemas.pipeline import PipelineInput, PipelineOutput, PolicyItem

logger = logging.getLogger(__name__)

HANDLERS: dict[Handler, Callable[[PipelineContext], Awaitable[Evidence]]] = {
    Handler.SEARCH: search.collect,
    Handler.DETAIL: detail.collect,
    Handler.SHORT: short.collect,
    Handler.CLARIFY: clarify.collect,
}


# 스트림 이벤트: 카드(한 번) → 본문 조각들 → PipelineOutput. 카드는 항상 첫 본문 조각 바로 앞에 나감 (계약 B9)
@dataclass(frozen=True)
class PolicyCards:
    policies: list[PolicyItem]


@dataclass(frozen=True)
class AnswerToken:
    text: str


type PipelineEvent = PolicyCards | AnswerToken | PipelineOutput


# /internal은 스트림을 끝까지 모아 한 번에 돌려줌. 채팅과 같은 코드라 결과와 trace가 같음
async def run_pipeline(inp: PipelineInput, deps: PipelineDeps) -> PipelineOutput:
    async for event in stream_pipeline(inp, deps):
        if isinstance(event, PipelineOutput):
            return event
    raise RuntimeError("파이프라인 스트림이 결과 없이 끝남")


async def stream_pipeline(inp: PipelineInput, deps: PipelineDeps) -> AsyncIterator[PipelineEvent]:
    started = perf_counter()
    ctx = PipelineContext(input=inp, deps=deps, query=inp.question)

    ctx.analysis = await analyzer.analyze(ctx)
    latency = ctx.trace.setdefault("latency_ms", {})
    latency["analyze"] = _elapsed_ms(started)
    ctx.trace["analysis"] = ctx.analysis.to_trace()
    ctx.query = ctx.analysis.standalone_question(inp.question)
    ctx.search_query = ctx.analysis.search_query(inp.question)
    ctx.current_policies = update_current_policies(inp, ctx.analysis)
    ctx.trace["current_policy_nos"] = [policy.policy_no for policy in ctx.current_policies]

    # 검색할지 되물을지가 질문 조건과 프로필의 차이로 정해져 핸들러를 고르기 전에 계산함
    conditions_started = perf_counter()
    ctx.conditions = build_conditions(ctx.analysis, inp.profile, await load_regions(ctx), await load_code_names(ctx))
    latency["conditions"] = _elapsed_ms(conditions_started)
    ctx.trace["conditions"] = ctx.conditions.to_trace()

    route = choose_route(ctx.analysis, ctx.conditions)
    ctx.answer_kind = route.answer_kind
    ctx.trace["handler"] = route.handler.value
    ctx.trace["answer_kind"] = route.answer_kind.value
    ctx.evidence = await HANDLERS[route.handler](ctx)
    # 평가 서버가 핸들러와 관계없이 같은 키로 읽어, 검색하지 않은 응답은 빈 검색 결과와 필터 미적용으로 둠
    ctx.trace.setdefault("retrieved_chunks", [])
    ctx.trace.setdefault("filtered_policy_nos", None)

    generate_started = perf_counter()
    evidence = ctx.evidence
    policies: list[PolicyItem] = []
    result: GenerationResult | None = None
    async for event in generate_stream(ctx):
        if isinstance(event, CardsDecided):
            if event.numbers is None:
                logger.warning("답변 첫 줄 카드 형식이 없어 후보 전체를 카드로 표시함")
            policies = build_cards(
                evidence.candidates, evidence.policies, event.numbers, ctx.today, evidence.region_labels
            )
            yield PolicyCards(policies)
        elif isinstance(event, AnswerDelta):
            # 질문을 받고 사용자가 첫 글자를 보기까지의 시간 (응답 속도 확인용)
            latency.setdefault("first_token", _elapsed_ms(started))
            yield AnswerToken(event.text)
        else:
            result = event
    if result is None:
        raise RuntimeError("답변 스트림이 결과 없이 끝남")
    latency["generate"] = _elapsed_ms(generate_started)

    ctx.trace.update({
        "contexts": result.contexts,
        "cards": {
            "candidate_nos": [group.policy_no for group in evidence.candidates],
            "selected": result.card_numbers,
            "fallback": result.card_numbers is None,
        },
        "retriever": deps.settings.retriever,
        "top_k": deps.settings.top_k,
        "prompt_version": result.prompt_version,
        "embedding_model": deps.llm.embedding_model,
        "chat_model": deps.llm.chat_model,
        "tokens": {"prompt": result.prompt_tokens, "completion": result.completion_tokens},
    })
    latency["total"] = _elapsed_ms(started)
    yield PipelineOutput(
        answer=result.answer, policies=policies, trace=ctx.trace, current_policies=ctx.current_policies
    )


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
