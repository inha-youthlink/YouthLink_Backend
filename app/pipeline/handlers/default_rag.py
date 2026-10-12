from time import perf_counter

from app.pipeline.context import PipelineContext
from app.pipeline.generator import generate
from app.pipeline.grouping import group_by_policy
from app.schemas.pipeline import PipelineOutput, PolicyItem


async def handle(ctx: PipelineContext) -> PipelineOutput:
    deps = ctx.deps
    latency = ctx.trace.setdefault("latency_ms", {})

    # 임베딩이 검색기 안에서 실행되어 임베딩과 벡터 검색을 합친 시간으로 기록함
    started = perf_counter()
    chunks = await deps.retriever.retrieve(ctx.query, None, deps.settings.top_k)
    ctx.groups = group_by_policy(chunks)
    latency["retrieve"] = _elapsed_ms(started)

    started = perf_counter()
    ctx.policies = await deps.policy_repository.get_by_ids([g.policy_no for g in ctx.groups])
    latency["policy_lookup"] = _elapsed_ms(started)

    started = perf_counter()
    result = await generate(ctx)
    latency["generate"] = _elapsed_ms(started)

    policies = [
        PolicyItem(
            policy_no=policy.policy_no,
            policy_name=policy.policy_name,
            application_start_date=policy.application_start_date,
            application_end_date=policy.application_end_date,
            application_url=policy.application_url,
            score=group.score,
        )
        for group in ctx.groups
        if (policy := ctx.policies.get(group.policy_no)) is not None
    ]

    ctx.trace.update({
        "retrieved_chunks": [
            {
                "chunk_id": str(chunk.chunk_id),
                "policy_no": chunk.policy_no,
                "chunk_type": chunk.chunk_type,
                "chunk_index": chunk.chunk_index,
                "score": chunk.score,
                "content": chunk.content,
            }
            for chunk in chunks
        ],
        "contexts": result.contexts,
        "retriever": deps.settings.retriever,
        "top_k": deps.settings.top_k,
        "prompt_version": result.prompt_version,
        "embedding_model": deps.llm.embedding_model,
        "chat_model": deps.llm.chat_model,
        "tokens": {"prompt": result.prompt_tokens, "completion": result.completion_tokens},
    })
    return PipelineOutput(answer=result.answer, policies=policies, trace=ctx.trace)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
