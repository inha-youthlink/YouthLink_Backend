import asyncio
from dataclasses import dataclass
from time import perf_counter

from app.pipeline.analyzer import ResolvedSubQuery
from app.pipeline.cards import COMPOSITE_MAX_CARDS, local_first, pick_candidates
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import NO_SCORE, CompareGroup, Evidence
from app.pipeline.grouping import PolicyGroup, group_by_policy
from app.pipeline.handlers.search import filtered_policy_nos, record_retrieval
from app.pipeline.router import SubKind, sub_kind
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import Chunk

# 복합 질문 (design.md 8-5): 하위 질문마다 검색·지목 중 하나로 근거를 모으고, 나머지는 이유만 표시해 답변 1회로 합침

# answer_composite_v1의 안내 규칙과 문구가 같아야 함
SKIP_MARK = "따로 답하지 않음"
SKIP_REASONS = {
    SubKind.OUT_OF_SCOPE: "청년 정책 질문이 아님",
    SubKind.UNCLEAR: "어느 정책인지 알 수 없음",
    SubKind.COMPARE: "비교는 따로",
}
CARD_LIMIT_REASON = "한 번에 보여 줄 정책 수를 넘음"


@dataclass(frozen=True)
class _Plan:
    number: int
    sub: ResolvedSubQuery
    kind: SubKind


@dataclass(frozen=True)
class _Slot:
    name: str
    fixed: bool
    ranked: list[PolicyGroup]


async def collect(ctx: PipelineContext) -> Evidence:
    deps = ctx.deps
    repository = deps.policy_repository
    latency = ctx.trace.setdefault("latency_ms", {})
    plans, lines = _plan(ctx.analysis.sub_queries)
    search_plans = [plan for plan in plans if plan.kind == SubKind.SEARCH]

    started = perf_counter()
    policy_ids = await filtered_policy_nos(ctx) if search_plans else None
    latency["filter"] = _elapsed_ms(started)

    # 하위 질문끼리 검색어가 달라 한 번에 검색하면 한쪽이 밀리므로 따로, 병렬로 검색함
    started = perf_counter()
    target_nos = list(dict.fromkeys(
        no for plan in plans if plan.kind == SubKind.TARGET for no in plan.sub.target_policy_nos
    ))
    searched, target_records = await asyncio.gather(
        asyncio.gather(*(_search(ctx, plan.sub, policy_ids) for plan in search_plans)),
        repository.get_by_ids(target_nos),
    )
    latency["retrieve"] = _elapsed_ms(started)

    started = perf_counter()
    policies: dict[str, PolicyRecord] = dict(target_records)
    by_search = dict(zip((plan.number for plan in search_plans), searched, strict=True))
    slots: list[_Slot] = []
    for plan in plans:
        name = f"질문 {plan.number}: {plan.sub.standalone_question}"
        if plan.kind == SubKind.SEARCH:
            ranked, records, _ = by_search[plan.number]
            policies.update(records)
            slots.append(_Slot(name, fixed=False, ranked=ranked))
        else:
            targets = [PolicyGroup(no, NO_SCORE, []) for no in plan.sub.target_policy_nos if no in target_records]
            slots.append(_Slot(name, fixed=True, ranked=targets))

    # 하위 질문마다 단일 질문과 같은 후보(상위 card_candidates개 → 지역 정책 먼저)를 만든 뒤 번갈아 고름
    # 고른 뒤에 지역 정렬을 하면 자리가 적은 묶음에서 지역 정책이 정렬 전에 잘림 (13주차 확인 C03 기후동행카드)
    limit = deps.settings.card_candidates
    slots = [slot if slot.fixed else _Slot(slot.name, False, slot.ranked[:limit]) for slot in slots]
    labeled_nos = list(dict.fromkeys(group.policy_no for slot in slots for group in slot.ranked))
    region_labels = await repository.get_region_labels(labeled_nos)
    if ctx.conditions and ctx.conditions.region is not None:
        sido_name = ctx.conditions.region.sido_name
        slots = [slot if slot.fixed else _Slot(slot.name, False, local_first(slot.ranked, region_labels, sido_name))
                 for slot in slots]
    picked = _pick(slots, policies, limit)
    candidate_nos = [group.policy_no for groups in picked for group in groups]
    documents = await repository.get_documents(candidate_nos)
    region_labels = {no: region_labels[no] for no in candidate_nos if no in region_labels}
    latency["policy_lookup"] = _elapsed_ms(started)

    if search_plans:
        record_retrieval(ctx, policy_ids, _unique_chunks([chunk for _, _, chunks in searched for chunk in chunks]))
    ctx.trace["composite"] = {
        "questions": lines,
        "groups": [
            {"name": slot.name, "fixed": slot.fixed, "policy_nos": [group.policy_no for group in chosen]}
            for slot, chosen in zip(slots, picked, strict=True)
        ],
    }
    return Evidence(
        candidates=[group for chosen in picked for group in chosen],
        policies={no: policies[no] for no in candidate_nos},
        documents=documents,
        region_labels=region_labels,
        # 고를 묶음이 없으면(모두 지목이거나 답할 하위 질문이 없음) 카드를 미리 정함
        fixed_cards=not search_plans,
        filtered=bool(search_plans),
        composite_groups=[
            CompareGroup(slot.name, len(chosen), slot.fixed) for slot, chosen in zip(slots, picked, strict=True)
        ],
        question_lines=lines,
    )


# 인사는 목록에서 빼고, 답하지 않을 하위 질문은 이유를 붙여 목록에 남김 (답변이 그 질문을 빠뜨리지 않게)
# 카드 자리(3개)를 넘는 하위 질문(지목을 여럿 한 경우)은 안내로 돌림. 첫 하위 질문은 넘쳐도 받음
def _plan(subs: list[ResolvedSubQuery]) -> tuple[list[_Plan], list[str]]:
    plans: list[_Plan] = []
    lines: list[str] = []
    cards = 0
    for sub in subs:
        kind = sub_kind(sub)
        if kind == SubKind.IGNORE:
            continue
        reason = SKIP_REASONS.get(kind)
        if reason is None:
            need = len(sub.targets) if kind == SubKind.TARGET else 1
            if plans and cards + need > COMPOSITE_MAX_CARDS:
                reason = CARD_LIMIT_REASON
            else:
                cards += need
        number = len(lines) + 1
        lines.append(f"{number}. {sub.standalone_question}" + (f" ({SKIP_MARK}: {reason})" if reason else ""))
        if reason is None:
            plans.append(_Plan(number, sub, kind))
    return plans, lines


# 검색 핸들러와 같은 방법 (상태 감점, 그 하위 질문의 분야 가산점, 같은 이름은 하나)
async def _search(
    ctx: PipelineContext, sub: ResolvedSubQuery, policy_ids: list[str] | None
) -> tuple[list[PolicyGroup], dict[str, PolicyRecord], list[Chunk]]:
    deps = ctx.deps
    chunks = await deps.retriever.retrieve(sub.search_query or sub.standalone_question, policy_ids, deps.settings.top_k)
    groups = group_by_policy(chunks)
    policies = await deps.policy_repository.get_by_ids([group.policy_no for group in groups])
    ranked = pick_candidates(groups, policies, ctx.today, len(groups), frozenset({sub.category}))
    return ranked, policies, chunks


# 후보 자리를 묶음마다 하나씩 번갈아 채워 한쪽 질문이 자리를 모두 가져가지 않게 함
# 지목한 정책도 자리를 차지해 근거 길이가 단일 질문과 비슷하게 유지됨
# 지목한 정책과 같은 정책·같은 이름은 검색 묶음에 다시 넣지 않음 (카드가 겹치지 않게)
def _pick(slots: list[_Slot], policies: dict[str, PolicyRecord], limit: int) -> list[list[PolicyGroup]]:
    picked: list[list[PolicyGroup]] = [list(slot.ranked) if slot.fixed else [] for slot in slots]
    used = {group.policy_no for slot in slots if slot.fixed for group in slot.ranked}
    used_names = {policies[no].policy_name for no in used}
    positions = [0] * len(slots)
    progressed = True
    while len(used) < limit and progressed:
        progressed = False
        for index, slot in enumerate(slots):
            if slot.fixed or len(used) >= limit:
                continue
            while positions[index] < len(slot.ranked):
                group = slot.ranked[positions[index]]
                positions[index] += 1
                name = policies[group.policy_no].policy_name
                if group.policy_no in used or name in used_names:
                    continue
                picked[index].append(group)
                used.add(group.policy_no)
                used_names.add(name)
                progressed = True
                break
    return picked


# 하위 질문끼리 같은 청크가 걸리면 평가에서 두 번 세지 않도록 높은 점수 하나만 남김
def _unique_chunks(chunks: list[Chunk]) -> list[Chunk]:
    best: dict[str, Chunk] = {}
    for chunk in chunks:
        key = str(chunk.chunk_id)
        if key not in best or chunk.score > best[key].score:
            best[key] = chunk
    return list(best.values())


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
