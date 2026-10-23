import logging
from time import perf_counter

from app.pipeline.cards import build_cards, pick_candidates
from app.pipeline.context import PipelineContext
from app.pipeline.generator import generate
from app.pipeline.grouping import group_by_policy
from app.schemas.pipeline import PipelineOutput

logger = logging.getLogger(__name__)


async def handle(ctx: PipelineContext) -> PipelineOutput:
    deps = ctx.deps
    latency = ctx.trace.setdefault("latency_ms", {})

    # 임베딩이 검색기 안에서 실행되어 임베딩과 벡터 검색을 합친 시간으로 기록함
    started = perf_counter()
    chunks = await deps.retriever.retrieve(ctx.query, None, deps.settings.top_k)
    ctx.groups = group_by_policy(chunks)
    latency["retrieve"] = _elapsed_ms(started)

    # 상태 정렬에는 검색된 정책 전체의 신청 기간이, 근거에는 후보의 청크 전체가 필요해 두 번 조회함
    started = perf_counter()
    ctx.policies = await deps.policy_repository.get_by_ids([g.policy_no for g in ctx.groups])
    ctx.candidates = pick_candidates(ctx.groups, ctx.policies, ctx.today, deps.settings.card_candidates)
    ctx.documents = await deps.policy_repository.get_documents([g.policy_no for g in ctx.candidates])
    latency["policy_lookup"] = _elapsed_ms(started)

    started = perf_counter()
    result = await generate(ctx)
    latency["generate"] = _elapsed_ms(started)

    if result.card_numbers is None:
        logger.warning("답변 첫 줄 카드 형식이 없어 후보 전체를 카드로 표시함")
    policies = build_cards(ctx.candidates, ctx.policies, result.card_numbers)

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
        "cards": {
            "candidate_nos": [group.policy_no for group in ctx.candidates],
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
    return PipelineOutput(answer=result.answer, policies=policies, trace=ctx.trace)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
