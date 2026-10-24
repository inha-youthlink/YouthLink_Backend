from app.pipeline.context import PipelineContext
from app.pipeline.evidence import NO_SCORE, Evidence
from app.pipeline.grouping import PolicyGroup

GENERAL_REPLY = (
    "질문을 정확히 이해하지 못했어요. 청년 정책이 궁금하시면 예를 들어 '서울 월세 지원 알려줘'처럼 말씀해 주세요. "
    "정책과 관계없는 질문은 도와드리기 어려워요."
)


# 답변의 번호가 다음 질문의 카드 번호가 되도록 보기로 든 정책을 같은 순서로 카드에 둠
async def collect(ctx: PipelineContext) -> Evidence:
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
