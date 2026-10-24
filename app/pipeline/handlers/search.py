from time import perf_counter

from app.pipeline.cards import pick_candidates, pick_split_candidates
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
    query, k = ctx.search_query or ctx.query, deps.settings.top_k
    split = ctx.conditions.region is not None and policy_ids is not None
    if split:
        nationwide = await deps.policy_repository.nationwide_policy_nos()
        local_ids = [no for no in policy_ids if no not in nationwide]
        national_ids = [no for no in policy_ids if no in nationwide]
        # 같은 검색어라 검색기가 임베딩을 재사용하도록 차례로 부름
        local_chunks = await deps.retriever.retrieve(query, local_ids, k)
        national_chunks = await deps.retriever.retrieve(query, national_ids, k)
        local_groups, national_groups = group_by_policy(local_chunks), group_by_policy(national_chunks)
        chunks = local_chunks + national_chunks
        groups = local_groups + national_groups
    else:
        chunks = await deps.retriever.retrieve(query, policy_ids, k)
        groups = group_by_policy(chunks)
    latency["retrieve"] = _elapsed_ms(started)

    # 상태 정렬에는 검색된 정책 전체의 신청 기간이, 근거에는 후보의 청크 전체가 필요해 두 번 조회함
    started = perf_counter()
    policies = await deps.policy_repository.get_by_ids([g.policy_no for g in groups])
    limit = deps.settings.card_candidates
    candidates = (
        pick_split_candidates(local_groups, national_groups, policies, ctx.today, limit) if split
        else pick_candidates(groups, policies, ctx.today, limit)
    )
    candidate_nos = [g.policy_no for g in candidates]
    documents = await deps.policy_repository.get_documents(candidate_nos)
    region_labels = await deps.policy_repository.get_region_labels(candidate_nos)
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
            "split_search": split,
        },
    })
    return Evidence(candidates=candidates, policies=policies, documents=documents, region_labels=region_labels)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
