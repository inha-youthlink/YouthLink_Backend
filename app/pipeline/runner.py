from time import perf_counter

from app.core.dependencies import PipelineDeps
from app.pipeline import analyzer
from app.pipeline.context import PipelineContext
from app.pipeline.current_policy import update_current_policies
from app.pipeline.handlers import default_rag
from app.schemas.pipeline import PipelineInput, PipelineOutput


async def run_pipeline(inp: PipelineInput, deps: PipelineDeps) -> PipelineOutput:
    started = perf_counter()
    ctx = PipelineContext(input=inp, deps=deps, query=inp.question)

    ctx.analysis = await analyzer.analyze(ctx)
    ctx.trace["latency_ms"] = {"analyze": _elapsed_ms(started)}
    ctx.trace["analysis"] = ctx.analysis.to_trace()
    ctx.query = ctx.analysis.standalone_question(inp.question)
    ctx.search_query = ctx.analysis.search_query(inp.question)
    ctx.current_policies = update_current_policies(inp, ctx.analysis)
    ctx.trace["current_policy_nos"] = [policy.policy_no for policy in ctx.current_policies]

    ctx.trace["handler"] = "default_rag"
    output = await default_rag.handle(ctx)

    # 질문 분석까지 포함한 시간이라 핸들러가 아닌 여기서 기록함
    output.trace["latency_ms"]["total"] = _elapsed_ms(started)
    output.current_policies = ctx.current_policies
    return output


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
