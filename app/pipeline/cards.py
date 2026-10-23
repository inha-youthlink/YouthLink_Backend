from datetime import date

from app.pipeline.generator import ApplicationStatus, application_status
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import PolicyItem

# 신청 가능한 정책을 먼저 보여 주고, 기간을 모르는 정책은 마감보다 앞에 둠
STATUS_RANK = {
    ApplicationStatus.OPEN: 0,
    ApplicationStatus.ALWAYS_OPEN: 0,
    ApplicationStatus.UPCOMING: 1,
    ApplicationStatus.UNKNOWN: 2,
    ApplicationStatus.CLOSED: 3,
}


def pick_candidates(
    groups: list[PolicyGroup], policies: dict[str, PolicyRecord], today: date, limit: int
) -> list[PolicyGroup]:
    known = [group for group in groups if group.policy_no in policies]
    # 정렬이 안정적이라 같은 상태끼리는 검색 점수 순서가 유지됨
    ranked = sorted(known, key=lambda group: STATUS_RANK[application_status(policies[group.policy_no], today)])
    return ranked[:limit]


# numbers가 None이면 LLM이 첫 줄 형식을 어긴 경우라 지금까지처럼 후보 전체를 보여 줌
def build_cards(
    candidates: list[PolicyGroup], policies: dict[str, PolicyRecord], numbers: list[int] | None
) -> list[PolicyItem]:
    chosen = candidates if numbers is None else [candidates[number - 1] for number in numbers]
    return [
        PolicyItem(
            policy_no=group.policy_no,
            policy_name=policy.policy_name,
            application_start_date=policy.application_start_date,
            application_end_date=policy.application_end_date,
            application_url=policy.application_url,
            score=group.score,
        )
        for group in chosen
        if (policy := policies.get(group.policy_no)) is not None
    ]
