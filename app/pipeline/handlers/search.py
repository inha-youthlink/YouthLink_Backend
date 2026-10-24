from time import perf_counter

from app.pipeline.cards import local_first, pick_candidates
from app.pipeline.conditions import build_conditions, load_code_names, load_regions
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import Evidence
from app.pipeline.grouping import group_by_policy


async def collect(ctx: PipelineContext) -> Evidence:
    deps = ctx.deps
    latency = ctx.trace.setdefault("latency_ms", {})

    started = perf_counter()
    regions = await load_regions(ctx)
    code_names = await load_code_names(ctx)
    ctx.conditions = build_conditions(ctx.analysis, ctx.input.profile, regions, code_names)
    policy_filter = ctx.conditions.to_filter()
    policy_ids = (
        None if policy_filter.is_empty()
        else await deps.policy_repository.filter_policy_nos(policy_filter, ctx.today)
    )
    latency["filter"] = _elapsed_ms(started)

    # 임베딩이 검색기 안에서 실행되어 임베딩과 벡터 검색을 합친 시간으로 기록함
    started = perf_counter()
    chunks = await deps.retriever.retrieve(ctx.search_query or ctx.query, policy_ids, deps.settings.top_k)
    groups = group_by_policy(chunks)
    latency["retrieve"] = _elapsed_ms(started)

    # 상태 감점에는 검색된 정책 전체의 신청 기간이, 근거에는 후보의 청크 전체가 필요해 두 번 조회함
    started = perf_counter()
    policies = await deps.policy_repository.get_by_ids([g.policy_no for g in groups])
    candidates = pick_candidates(groups, policies, ctx.today, deps.settings.card_candidates)
    candidate_nos = [g.policy_no for g in candidates]
    documents = await deps.policy_repository.get_documents(candidate_nos)
    region_labels = await deps.policy_repository.get_region_labels(candidate_nos)
    # 근거 순서가 카드 순서라 지역 조건이 있으면 그 시도 정책을 앞에 둠
    if ctx.conditions.region is not None:
        candidates = local_first(candidates, region_labels, ctx.conditions.region.label)
    latency["policy_lookup"] = _elapsed_ms(started)

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
        "conditions": {
            "lines": ctx.conditions.lines,
            "filtered_policy_count": None if policy_ids is None else len(policy_ids),
        },
    })
    return Evidence(candidates=candidates, policies=policies, documents=documents, region_labels=region_labels)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
