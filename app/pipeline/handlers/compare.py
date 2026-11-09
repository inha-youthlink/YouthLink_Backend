import asyncio
from dataclasses import dataclass
from time import perf_counter

from app.pipeline.analyzer import ResolvedSubQuery
from app.pipeline.cards import pick_candidates
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import NO_SCORE, AnswerKind, CompareGroup, Evidence
from app.pipeline.grouping import PolicyGroup, group_by_policy
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import Chunk

# 검색 1위가 틀리는 경우가 많아(이름 20개 중 7개) 대상마다 여러 후보를 주고 답변 LLM이 이름·지역을 보고 고름
CANDIDATES_PER_TARGET = 3
# 이름 일치는 후보를 늘리는 데만 써서, 흔한 말("월세 지원")이 걸려도 순위에 쓸 만큼만 가져옴
NAME_MATCH_LIMIT = 20

TOO_MANY_HEAD = "비교는 두 정책씩 할 수 있어요."


@dataclass(frozen=True)
class _Found:
    ranked: list[PolicyGroup]
    policies: dict[str, PolicyRecord]
    chunks: list[Chunk]
    name_match_count: int


async def collect(ctx: PipelineContext) -> Evidence:
    sub = ctx.analysis.sub_queries[0]
    if ctx.answer_kind == AnswerKind.CLARIFY:
        return await _too_many(ctx, sub)

    repository = ctx.deps.policy_repository
    latency = ctx.trace.setdefault("latency_ms", {})
    started = perf_counter()
    target_nos = sub.target_policy_nos
    policy_ids = await _filtered_policy_nos(ctx) if sub.compare_queries else None
    found = await asyncio.gather(*(_search(ctx, query, policy_ids) for query in sub.compare_queries))
    latency["compare_search"] = _elapsed_ms(started)

    started = perf_counter()
    target_records = await repository.get_by_ids(target_nos)
    candidates: list[PolicyGroup] = []
    groups: list[CompareGroup] = []
    for no in target_nos:
        if no in target_records:
            candidates.append(PolicyGroup(no, NO_SCORE, []))
            groups.append(CompareGroup(target_records[no].policy_name, 1, fixed=True))
    # 같은 정책이 두 대상의 후보에 함께 있으면 LLM이 같은 정책끼리 비교할 수 있어 앞 대상에만 둠
    used = {group.policy_no for group in candidates}
    policies = dict(target_records)
    query_traces = []
    for query, result in zip(sub.compare_queries, found):
        picked = [group for group in result.ranked if group.policy_no not in used][:CANDIDATES_PER_TARGET]
        used.update(group.policy_no for group in picked)
        candidates += picked
        groups.append(CompareGroup(query, len(picked), fixed=False))
        policies.update(result.policies)
        query_traces.append({
            "query": query,
            "candidate_nos": [group.policy_no for group in picked],
            "name_match_count": result.name_match_count,
        })
    candidate_nos = [group.policy_no for group in candidates]
    documents = await repository.get_documents(candidate_nos)
    region_labels = await repository.get_region_labels(candidate_nos)
    latency["policy_lookup"] = _elapsed_ms(started)

    ctx.trace["compare"] = {"target_policy_nos": target_nos, "queries": query_traces}
    if sub.compare_queries:
        # 평가 서버가 검색 핸들러와 같은 키로 검색 결과를 읽음. 대상별 검색 결과를 이어 붙임
        ctx.trace["filtered_policy_nos"] = policy_ids
        ctx.trace["retrieved_chunks"] = [
            {
                "chunk_id": str(chunk.chunk_id),
                "policy_no": chunk.policy_no,
                "chunk_type": chunk.chunk_type,
                "chunk_index": chunk.chunk_index,
                "score": chunk.score,
                "content": chunk.content,
            }
            for result in found
            for chunk in result.chunks
        ]
    return Evidence(
        candidates=candidates,
        policies={no: policies[no] for no in candidate_nos},
        documents=documents,
        region_labels=region_labels,
        # 모두 지목했으면 고를 것이 없어 카드를 미리 정함
        fixed_cards=not sub.compare_queries,
        filtered=bool(sub.compare_queries),
        compare_groups=groups,
    )


async def _filtered_policy_nos(ctx: PipelineContext) -> list[str] | None:
    policy_filter = ctx.conditions.to_filter()
    if policy_filter.is_empty():
        return None
    return await ctx.deps.policy_repository.filter_policy_nos(policy_filter, ctx.today)


# 이름이 맞는 정책을 먼저, 남는 자리는 벡터 순위로 채움. 둘 다 상태 감점을 적용하고 같은 이름은 하나만 남김
async def _search(ctx: PipelineContext, query: str, policy_ids: list[str] | None) -> _Found:
    deps = ctx.deps
    chunks, name_nos = await asyncio.gather(
        deps.retriever.retrieve(query, policy_ids, deps.settings.top_k),
        deps.policy_repository.find_nos_by_name(query, policy_ids, NAME_MATCH_LIMIT),
    )
    vector_groups = group_by_policy(chunks)
    vector_nos = {group.policy_no for group in vector_groups}
    name_only = [PolicyGroup(no, NO_SCORE, []) for no in name_nos if no not in vector_nos]
    policies = await deps.policy_repository.get_by_ids([*vector_nos, *(group.policy_no for group in name_only)])

    by_vector = pick_candidates(vector_groups, policies, ctx.today, len(vector_groups))
    name_set = set(name_nos)
    ordered = [
        *(group for group in by_vector if group.policy_no in name_set),
        *pick_candidates(name_only, policies, ctx.today, len(name_only)),
        *(group for group in by_vector if group.policy_no not in name_set),
    ]
    ranked: list[PolicyGroup] = []
    names: set[str] = set()
    for group in ordered:
        name = policies[group.policy_no].policy_name
        if name not in names:
            names.add(name)
            ranked.append(group)
    return _Found(ranked=ranked, policies=policies, chunks=chunks, name_match_count=len(name_nos))


# 대상이 셋 이상이면 어느 둘인지 짐작하지 않고 되물음. 모두 지목했으면 그 정책들을 보기 카드로 둠
async def _too_many(ctx: PipelineContext, sub: ResolvedSubQuery) -> Evidence:
    ctx.trace["clarify_reason"] = "compare_limit"
    repository = ctx.deps.policy_repository
    policies = await repository.get_by_ids(sub.target_policy_nos)
    targets = [no for no in sub.target_policy_nos if no in policies]
    if sub.compare_queries or len(targets) < 2:
        names = [policies[no].policy_name for no in targets] + sub.compare_queries
        # 이름 끝 받침에 따라 조사가 달라져 조사 없이 씀. 지목한 정책이 사라져 이름이 둘도 안 남으면 예시를 뺌
        lines = [TOO_MANY_HEAD]
        if names:
            lines.append(f"말씀하신 정책: {', '.join(names)}")
        lines.append(
            f"이 중 두 개를 골라 '{names[0]}, {names[1]} 비교해 줘'처럼 다시 물어봐 주세요."
            if len(names) >= 2 else "비교할 두 정책의 이름을 함께 말씀해 주세요."
        )
        return Evidence(fixed_cards=True, reply="\n".join(lines))

    region_labels = await repository.get_region_labels(targets)
    lines = [f"{order}. {policies[no].policy_name}" for order, no in enumerate(targets, start=1)]
    reply = "\n".join([
        f"{TOO_MANY_HEAD} 비교할 두 정책을 골라 주세요.",
        *lines,
        "예: '1번이랑 2번 비교해 줘'",
    ])
    return Evidence(
        candidates=[PolicyGroup(no, NO_SCORE, []) for no in targets],
        policies={no: policies[no] for no in targets},
        region_labels=region_labels,
        fixed_cards=True,
        reply=reply,
    )


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
