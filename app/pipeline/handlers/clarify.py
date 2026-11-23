from app.pipeline.conditions import AGE, REGION, Mismatch
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import NO_SCORE, Evidence
from app.pipeline.grouping import PolicyGroup

# 예시에 지역을 넣으면 프로필 지역이 다른 사용자가 따라 했을 때 되묻기로 이어져 지역을 뺌
# "제일 이득인 거 하나만"처럼 분야도 기준도 정할 수 없는 찾기 질문은 단발 검색보다 질문을 주고받으며 좁히는
# 에이전트가 맞아 안내함. 에이전트 이름은 프론트 패널 버튼과 같아야 함
AGENT_NAME = "정책 추천 에이전트"
GENERAL_REPLY = (
    "어떤 정책을 찾으시는지 정확히 알기 어려워요. "
    f"무엇이 맞을지 모르겠다면 오른쪽 **{AGENT_NAME}**에서 질문에 답하며 찾아볼 수 있어요. "
    "바로 물어보시려면 '월세 지원 알려줘'처럼 말씀해 주세요. 정책과 관계없는 질문은 도와드리기 어려워요."
)


async def collect(ctx: PipelineContext) -> Evidence:
    mismatches = ctx.conditions.ask_back if ctx.conditions else []
    if mismatches:
        ctx.trace["clarify_reason"] = "condition"
        return Evidence(fixed_cards=True, reply=condition_reply(mismatches))
    ctx.trace["clarify_reason"] = "policy"
    return await _policy_options(ctx)


# 상태를 저장하지 않아 다음 질문에서 고를 수 있도록 다시 묻는 방법을 안내함
def condition_reply(mismatches: list[Mismatch]) -> str:
    by_name = {mismatch.name: mismatch for mismatch in mismatches}
    region, age = by_name.get(REGION), by_name.get(AGE)
    if region and age:
        first = (
            f"질문에 말씀하신 지역({region.question})과 나이({age.question})가 "
            f"프로필({region.profile}, {age.profile})과 달라요."
        )
        removed = "지역과 나이를"
    elif region:
        first = f"질문에 말씀하신 지역({region.question})이 프로필 지역({region.profile})과 달라요."
        removed = "지역을"
    else:
        first = f"질문에 말씀하신 나이({age.question})가 프로필 나이({age.profile})와 달라요."
        removed = "나이를"
    return "\n".join([
        first,
        f"- 프로필 기준으로 찾으려면 {removed} 빼고 다시 물어봐 주세요.",
        "- 말씀하신 조건으로 찾으려면 프로필을 그 조건으로 정한 새 채팅방에서 물어봐 주세요.",
    ])


# 답변의 번호가 다음 질문의 카드 번호가 되도록 보기로 든 정책을 같은 순서로 카드에 둠
async def _policy_options(ctx: PipelineContext) -> Evidence:
    history = ctx.input.history
    options = ctx.current_policies or (history[-1].policies if history else [])
    if not options:
        return Evidence(fixed_cards=True, reply=GENERAL_REPLY)

    repository = ctx.deps.policy_repository
    policies = await repository.get_by_ids([policy.policy_no for policy in options])
    options = [policy for policy in options if policy.policy_no in policies]
    if not options:
        return Evidence(fixed_cards=True, reply=GENERAL_REPLY)

    region_labels = await repository.get_region_labels([policy.policy_no for policy in options])
    lines = [f"{order}. {policy.policy_name}" for order, policy in enumerate(options, start=1)]
    return Evidence(
        candidates=[PolicyGroup(policy.policy_no, NO_SCORE, []) for policy in options],
        policies=policies,
        region_labels=region_labels,
        fixed_cards=True,
        reply="\n".join(["어느 정책을 말씀하시나요?", *lines, "번호나 정책 이름으로 말씀해 주세요."]),
    )
