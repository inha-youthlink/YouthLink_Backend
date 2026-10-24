from time import perf_counter

from app.pipeline.context import PipelineContext
from app.pipeline.evidence import NO_SCORE, Evidence
from app.pipeline.grouping import PolicyGroup


# 검색을 거치면 다른 지역의 같은 이름 정책이 섞여 지목한 정책만 근거로 씀
async def collect(ctx: PipelineContext) -> Evidence:
    repository = ctx.deps.policy_repository
    started = perf_counter()
    target_nos = list(dict.fromkeys(
        policy.policy_no for sub in ctx.analysis.sub_queries for policy in sub.targets
    ))
    policies = await repository.get_by_ids(target_nos)
    candidate_nos = [no for no in target_nos if no in policies]
    documents = await repository.get_documents(candidate_nos)
    region_labels = await repository.get_region_labels(candidate_nos)
    ctx.trace.setdefault("latency_ms", {})["policy_lookup"] = int((perf_counter() - started) * 1000)
    return Evidence(
        candidates=[PolicyGroup(no, NO_SCORE, []) for no in candidate_nos],
        policies=policies,
        documents=documents,
        region_labels=region_labels,
        fixed_cards=True,
    )
