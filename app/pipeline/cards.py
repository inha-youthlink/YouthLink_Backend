from datetime import date

from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import PolicyItem
from app.schemas.policy_info import ApplicationStatus, application_status, clean_url

# 신청 가능한 정책을 먼저 보여 주고, 기간을 모르는 정책은 마감보다 앞에 둠
STATUS_RANK = {
    ApplicationStatus.OPEN: 0,
    ApplicationStatus.ALWAYS_OPEN: 0,
    ApplicationStatus.UPCOMING: 1,
    ApplicationStatus.UNKNOWN: 2,
    ApplicationStatus.CLOSED: 3,
}

# 1위가 2위보다 이만큼 높으면 질문이 그 정책을 가리킨다고 봄. 실제 데이터 예: 이름을 직접 물은 질문 0.788 대 0.633,
# 일반 목록 질문 0.500 대 0.495. 기준값은 신청 상태 우선순위 PR에서 실제 질문들로 다시 정함
CLEAR_TOP_MARGIN = 0.05
# 같은 이름이 지역별로 많은 정책("청년 월세 지원")에서 후보를 다 차지하지 않도록 제한함
MAX_CLEAR_TOP = 2


def pick_candidates(
    groups: list[PolicyGroup], policies: dict[str, PolicyRecord], today: date, limit: int
) -> list[PolicyGroup]:
    known = [group for group in groups if group.policy_no in policies]

    def rank(group: PolicyGroup) -> int:
        return STATUS_RANK[status_of(policies[group.policy_no], today)]

    # 정책 이름을 직접 물으면 그 정책이 확실한 1위인데, 마감·예정이면 상태 정렬로 밀려 "찾지 못했다"가 됨
    # 확실한 1위를 먼저 자리에 넣고 나머지를 상태 순으로 채운 뒤, 전체를 다시 상태 순으로 둠
    # 정렬이 안정적이라 같은 상태끼리는 검색 점수 순서가 유지됨
    top = _clear_top(known, policies)
    rest = [group for group in sorted(known, key=rank) if group not in top]
    return sorted([*top, *rest][:limit], key=rank)


# 같은 이름은 같은 사업의 연도·지역별 공고라 한 묶음으로 보고, 이름이 다른 정책과 점수 차이를 비교함
# (예: 경기도 청년기본소득 2025 마감 0.689, 2026 예정 0.666, 다음 정책 0.630)
def _clear_top(groups: list[PolicyGroup], policies: dict[str, PolicyRecord]) -> list[PolicyGroup]:
    if not groups:
        return []
    name = policies[groups[0].policy_no].policy_name
    same = [group for group in groups if policies[group.policy_no].policy_name == name]
    others = [group for group in groups if policies[group.policy_no].policy_name != name]
    if others and groups[0].score - others[0].score < CLEAR_TOP_MARGIN:
        return []
    return same[:MAX_CLEAR_TOP]


def status_of(policy: PolicyRecord, today: date) -> ApplicationStatus:
    return application_status(
        policy.application_period_type_code, policy.application_start_date, policy.application_end_date, today
    )


# numbers가 None이면 LLM이 첫 줄 형식을 어긴 경우라 지금까지처럼 후보 전체를 보여 줌
def build_cards(
    candidates: list[PolicyGroup], policies: dict[str, PolicyRecord], numbers: list[int] | None, today: date
) -> list[PolicyItem]:
    chosen = candidates if numbers is None else [candidates[number - 1] for number in numbers]
    return [
        PolicyItem(
            policy_no=group.policy_no,
            policy_name=policy.policy_name,
            application_start_date=policy.application_start_date,
            application_end_date=policy.application_end_date,
            application_url=clean_url(policy.application_url),
            score=group.score,
            application_status=status_of(policy, today),
            supervising_org_name=policy.supervising_org_name,
        )
        for group in chosen
        if (policy := policies.get(group.policy_no)) is not None
    ]
