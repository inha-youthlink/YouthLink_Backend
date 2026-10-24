from datetime import date

from app.pipeline.cards import build_cards, local_first, pick_candidates
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.analysis import Category
from app.schemas.policy_info import ALWAYS_OPEN_CODE, CLOSED_CODE, ApplicationStatus

TODAY = date(2026, 10, 23)


def _record(policy_no: str, code: str | None = None, start: date | None = None, end: date | None = None):
    return PolicyRecord(
        policy_no=policy_no,
        policy_name=f"정책 {policy_no}",
        application_period_type_code=code,
        application_start_date=start,
        application_end_date=end,
        application_url=f"https://example.com/{policy_no}",
        reference_url_1=None,
        reference_url_2=None,
        supervising_org_name=f"기관 {policy_no}",
    )


def _groups(*policy_nos: str) -> list[PolicyGroup]:
    # 검색 점수 내림차순으로 들어오는 묶기 결과를 흉내 냄
    return [PolicyGroup(policy_no=no, score=1 - index * 0.1, chunks=[]) for index, no in enumerate(policy_nos)]


POLICIES = {
    record.policy_no: record
    for record in [
        _record("CLOSED", CLOSED_CODE),
        _record("UNKNOWN"),
        _record("UPCOMING", start=date(2026, 11, 1), end=date(2026, 11, 30)),
        _record("OPEN", start=date(2026, 10, 1), end=date(2026, 10, 31)),
        _record("ALWAYS", ALWAYS_OPEN_CODE),
        _record("ENDED", start=date(2026, 9, 1), end=date(2026, 10, 22)),
    ]
}


def test_similar_scores_follow_status():
    # 점수 차가 감점보다 작으면 신청 가능·상시가 앞서고, 예정·정보 없음이 마감보다 앞섬. 기간이 지난 정책은 마감으로 봄
    groups = [
        PolicyGroup("CLOSED", 0.50, []), PolicyGroup("UNKNOWN", 0.50, []), PolicyGroup("UPCOMING", 0.49, []),
        PolicyGroup("OPEN", 0.49, []), PolicyGroup("ALWAYS", 0.48, []), PolicyGroup("ENDED", 0.48, []),
    ]

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=10)

    assert [c.policy_no for c in candidates] == ["OPEN", "ALWAYS", "UNKNOWN", "UPCOMING", "CLOSED", "ENDED"]


def test_much_more_relevant_closed_policy_stays_first():
    # 이름을 직접 물은 마감 정책처럼 점수 차가 감점보다 크면 상태와 관계없이 앞섬
    groups = [PolicyGroup("CLOSED", 0.79, []), PolicyGroup("OPEN", 0.63, []), PolicyGroup("ALWAYS", 0.62, [])]

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=2)

    assert [c.policy_no for c in candidates] == ["CLOSED", "OPEN"]


def test_candidates_limited_after_penalty():
    groups = [PolicyGroup("CLOSED", 0.60, []), PolicyGroup("UNKNOWN", 0.59, []), PolicyGroup("OPEN", 0.57, [])]

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=2)

    assert [c.policy_no for c in candidates] == ["OPEN", "UNKNOWN"]


def test_same_name_keeps_higher_adjusted_one():
    # 같은 이름의 두 공고(마감, 예정) 중 감점 뒤 점수가 높은 예정 공고만 남김
    policies = {
        **POLICIES,
        "NEXT-YEAR": PolicyRecord(
            policy_no="NEXT-YEAR", policy_name="정책 CLOSED", application_period_type_code=None,
            application_start_date=date(2026, 12, 1), application_end_date=date(2026, 12, 31),
            application_url=None, reference_url_1=None, reference_url_2=None, supervising_org_name=None,
        ),
    }
    groups = [
        PolicyGroup("CLOSED", 0.689, []), PolicyGroup("NEXT-YEAR", 0.680, []), PolicyGroup("OPEN", 0.630, []),
    ]

    candidates = pick_candidates(groups, policies, TODAY, limit=3)

    assert [c.policy_no for c in candidates] == ["NEXT-YEAR", "OPEN"]


def test_candidates_skip_policies_without_record():
    groups = _groups("MISSING", "OPEN")

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=5)

    assert [c.policy_no for c in candidates] == ["OPEN"]


def test_cards_follow_selected_numbers():
    candidates = _groups("OPEN", "ALWAYS", "UPCOMING")

    cards = build_cards(candidates, POLICIES, [1, 3], TODAY)

    assert [card.policy_no for card in cards] == ["OPEN", "UPCOMING"]
    assert cards[0].policy_name == "정책 OPEN"
    assert cards[0].application_end_date == date(2026, 10, 31)
    assert cards[1].score == candidates[2].score
    assert [card.application_status for card in cards] == [ApplicationStatus.OPEN, ApplicationStatus.UPCOMING]
    assert cards[0].supervising_org_name == "기관 OPEN"


def test_cards_empty_when_nothing_selected():
    assert build_cards(_groups("OPEN", "ALWAYS"), POLICIES, [], TODAY) == []


def test_cards_fall_back_to_all_candidates():
    cards = build_cards(_groups("OPEN", "ALWAYS"), POLICIES, None, TODAY)

    assert [card.policy_no for card in cards] == ["OPEN", "ALWAYS"]


def test_candidates_keep_one_per_policy_name():
    # 같은 이름(연도·지역별 공고)이 후보 칸을 나눠 쓰지 않음
    policies = {
        **POLICIES,
        "OPEN-2": PolicyRecord(
            policy_no="OPEN-2", policy_name="정책 OPEN", application_period_type_code=ALWAYS_OPEN_CODE,
            application_start_date=None, application_end_date=None,
            application_url=None, reference_url_1=None, reference_url_2=None, supervising_org_name=None,
        ),
    }
    groups = [
        PolicyGroup("OPEN", 0.60, []), PolicyGroup("OPEN-2", 0.59, []),
        PolicyGroup("ALWAYS", 0.58, []), PolicyGroup("UNKNOWN", 0.57, []),
    ]

    candidates = pick_candidates(groups, policies, TODAY, limit=3)

    assert [c.policy_no for c in candidates] == ["OPEN", "ALWAYS", "UNKNOWN"]


def test_local_first_keeps_candidates_and_order_within_groups():
    candidates = _groups("NATION", "SEOUL", "MULTI", "GANGNAM", "BUSAN")
    labels = {
        "NATION": "전국", "SEOUL": "서울특별시", "MULTI": "서울특별시 외 14개 시도",
        "GANGNAM": "서울특별시 강남구", "BUSAN": "부산광역시",
    }

    ordered = local_first(candidates, labels, "서울특별시")

    # 그 시도 정책만 앞으로 오고, 여러 시도에 걸친 정책은 지역 정책으로 보지 않음
    assert [c.policy_no for c in ordered] == ["SEOUL", "GANGNAM", "NATION", "MULTI", "BUSAN"]


def test_category_bonus_lifts_same_category_policy():
    def record(policy_no: str, categories: list[str]) -> PolicyRecord:
        return PolicyRecord(
            policy_no=policy_no, policy_name=policy_no, application_period_type_code=ALWAYS_OPEN_CODE,
            application_start_date=None, application_end_date=None, application_url=None,
            reference_url_1=None, reference_url_2=None, supervising_org_name=None, large_categories=categories,
        )

    # 옛 분류(복지문화)와 새 분류(금융･복지･문화)를 같은 분야로 봄
    policies = {
        "JOB": record("JOB", ["일자리"]), "OLD": record("OLD", ["복지문화"]), "NEW": record("NEW", ["금융･복지･문화"]),
    }
    groups = [PolicyGroup("JOB", 0.60, []), PolicyGroup("OLD", 0.58, []), PolicyGroup("NEW", 0.57, [])]

    lifted = pick_candidates(groups, policies, TODAY, limit=2, categories=frozenset({Category.WELFARE_CULTURE}))
    plain = pick_candidates(groups, policies, TODAY, limit=2)

    assert [c.policy_no for c in lifted] == ["OLD", "NEW"]
    assert [c.policy_no for c in plain] == ["JOB", "OLD"]
