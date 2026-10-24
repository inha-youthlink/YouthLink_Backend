from datetime import date

from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.analysis import Category
from app.schemas.pipeline import PolicyItem
from app.schemas.policy_info import ApplicationStatus, application_status

# 관련도가 비슷하면 신청 가능한 정책이 앞서고, 관련도가 크게 높으면 마감 정책도 남도록 순위 대신 감점을 씀
# 감점 값은 평가 세트(질문 25개)로 정한 임시 기준값
STATUS_PENALTY = {
    ApplicationStatus.OPEN: 0.0,
    ApplicationStatus.ALWAYS_OPEN: 0.0,
    ApplicationStatus.UPCOMING: 0.025,
    ApplicationStatus.UNKNOWN: 0.025,
    ApplicationStatus.CLOSED: 0.05,
}

# 넓은 질문에서 같은 분야 정책을 조금 앞세움. 거르지 않아 분야가 엇갈리게 분류된 정책도 남음 (평가 세트로 정한 임시 값)
CATEGORY_BONUS = 0.04
# 정책 대분류에 옛 분류와 새 분류가 섞여 있어 둘 다 맞춤
CATEGORY_NAMES = {
    Category.JOB: frozenset({"일자리"}),
    Category.HOUSING: frozenset({"주거"}),
    Category.EDUCATION: frozenset({"교육･직업훈련", "교육"}),
    Category.WELFARE_CULTURE: frozenset({"금융･복지･문화", "복지문화"}),
    Category.PARTICIPATION: frozenset({"참여･기반", "참여권리"}),
}


def pick_candidates(
    groups: list[PolicyGroup],
    policies: dict[str, PolicyRecord],
    today: date,
    limit: int,
    categories: frozenset[Category] = frozenset(),
) -> list[PolicyGroup]:
    known = [group for group in groups if group.policy_no in policies]
    wanted = frozenset().union(*(CATEGORY_NAMES.get(category, frozenset()) for category in categories))

    def adjusted(group: PolicyGroup) -> float:
        policy = policies[group.policy_no]
        bonus = CATEGORY_BONUS if wanted & set(policy.large_categories) else 0.0
        return group.score - STATUS_PENALTY[status_of(policy, today)] + bonus

    # 같은 이름은 연도·지역별 공고라 후보 칸을 나눠 쓰지 않도록 앞선 하나만 남김
    candidates: list[PolicyGroup] = []
    names: set[str] = set()
    for group in sorted(known, key=adjusted, reverse=True):
        name = policies[group.policy_no].policy_name
        if name in names:
            continue
        names.add(name)
        candidates.append(group)
        if len(candidates) == limit:
            break
    return candidates


# 후보는 그대로 두고 순서만 바꿔 재현율에 영향이 없음. "서울특별시 외 14개 시도"처럼 여러 시도에 걸친 정책은 지역 정책으로 보지 않음
def local_first(candidates: list[PolicyGroup], region_labels: dict[str, str], sido_name: str) -> list[PolicyGroup]:
    def is_local(group: PolicyGroup) -> bool:
        label = region_labels.get(group.policy_no, "")
        return label.startswith(sido_name) and " 외 " not in label

    return sorted(candidates, key=lambda group: not is_local(group))


def status_of(policy: PolicyRecord, today: date) -> ApplicationStatus:
    return application_status(
        policy.application_period_type_code, policy.application_start_date, policy.application_end_date, today
    )


# numbers가 None이면 LLM이 첫 줄 형식을 어긴 경우라 지금까지처럼 후보 전체를 보여 줌
def build_cards(
    candidates: list[PolicyGroup],
    policies: dict[str, PolicyRecord],
    numbers: list[int] | None,
    today: date,
    region_labels: dict[str, str] | None = None,
) -> list[PolicyItem]:
    chosen = candidates if numbers is None else [candidates[number - 1] for number in numbers]
    return [
        policy.to_item(group.score, today, (region_labels or {}).get(group.policy_no))
        for group in chosen
        if (policy := policies.get(group.policy_no)) is not None
    ]
