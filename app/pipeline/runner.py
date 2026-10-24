import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from app.core.dependencies import PipelineDeps
from app.pipeline import analyzer
from app.pipeline.cards import build_cards
from app.pipeline.context import PipelineContext
from app.pipeline.current_policy import update_current_policies
from app.pipeline.evidence import Evidence
from app.pipeline.generator import generate
from app.pipeline.handlers import clarify, detail, search, short
from app.pipeline.router import Handler, choose_route
from app.schemas.pipeline import PipelineInput, PipelineOutput

logger = logging.getLogger(__name__)

HANDLERS: dict[Handler, Callable[[PipelineContext], Awaitable[Evidence]]] = {
    Handler.SEARCH: search.collect,
    Handler.DETAIL: detail.collect,
    Handler.SHORT: short.collect,
    Handler.CLARIFY: clarify.collect,
}


async def run_pipeline(inp: PipelineInput, deps: PipelineDeps) -> PipelineOutput:
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

    route = choose_route(ctx.analysis)
    ctx.answer_kind = route.answer_kind
    ctx.trace["handler"] = route.handler.value
    ctx.trace["answer_kind"] = route.answer_kind.value
    ctx.evidence = await HANDLERS[route.handler](ctx)

    generate_started = perf_counter()
    result = await generate(ctx)
    latency["generate"] = _elapsed_ms(generate_started)

    evidence = ctx.evidence
    if result.card_numbers is None:
        logger.warning("답변 첫 줄 카드 형식이 없어 후보 전체를 카드로 표시함")
    policies = build_cards(evidence.candidates, evidence.policies, result.card_numbers, ctx.today, evidence.region_labels)
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
    return PipelineOutput(
        answer=result.answer, policies=policies, trace=ctx.trace, current_policies=ctx.current_policies
    )


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
