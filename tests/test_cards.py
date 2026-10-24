from datetime import date

from app.pipeline.cards import build_cards, pick_candidates, pick_split_candidates
from app.schemas.policy_info import ALWAYS_OPEN_CODE, CLOSED_CODE, ApplicationStatus
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord

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


def test_candidates_sorted_by_status_then_score():
    groups = _groups("CLOSED", "UNKNOWN", "UPCOMING", "OPEN", "ALWAYS", "ENDED")

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=10)

    # 신청 가능과 상시는 같은 순위라 점수 순서(OPEN이 먼저)를 유지함. 기간이 지난 정책은 마감으로 봄
    assert [c.policy_no for c in candidates] == ["OPEN", "ALWAYS", "UPCOMING", "UNKNOWN", "CLOSED", "ENDED"]


def test_candidates_limited_after_sorting():
    groups = _groups("UNKNOWN", "CLOSED", "OPEN")

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=2)

    assert [c.policy_no for c in candidates] == ["OPEN", "UNKNOWN"]


def test_candidates_keep_clear_top_regardless_of_status():
    # 마감 정책이 2위보다 확실히 높은 1위면 상태 정렬로 밀려도 후보에 남고, 순서는 상태 순
    groups = [
        PolicyGroup("CLOSED", 0.79, []),
        PolicyGroup("OPEN", 0.63, []),
        PolicyGroup("ALWAYS", 0.62, []),
        PolicyGroup("UNKNOWN", 0.61, []),
    ]

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=2)

    assert [c.policy_no for c in candidates] == ["OPEN", "CLOSED"]


def test_clear_top_counts_same_name_policies_together():
    # 같은 이름의 두 공고(마감, 예정)가 1·2위면 한 묶음으로 보고 다음 정책과 비교하며, 상태가 앞선 예정 공고만 남김
    policies = {
        **POLICIES,
        "NEXT-YEAR": PolicyRecord(
            policy_no="NEXT-YEAR", policy_name="정책 CLOSED", application_period_type_code=None,
            application_start_date=date(2026, 12, 1), application_end_date=date(2026, 12, 31),
            application_url=None, reference_url_1=None, reference_url_2=None, supervising_org_name=None,
        ),
    }
    groups = [
        PolicyGroup("CLOSED", 0.689, []),
        PolicyGroup("NEXT-YEAR", 0.666, []),
        PolicyGroup("OPEN", 0.630, []),
        PolicyGroup("ALWAYS", 0.618, []),
    ]

    candidates = pick_candidates(groups, policies, TODAY, limit=3)

    assert [c.policy_no for c in candidates] == ["OPEN", "ALWAYS", "NEXT-YEAR"]


def test_candidates_follow_status_when_top_is_not_clear():
    # 점수가 비슷하면 확실한 1위가 없어 신청 가능한 정책이 우선
    groups = [
        PolicyGroup("CLOSED", 0.500, []),
        PolicyGroup("OPEN", 0.495, []),
        PolicyGroup("ALWAYS", 0.490, []),
    ]

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=2)

    assert [c.policy_no for c in candidates] == ["OPEN", "ALWAYS"]


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



def test_split_candidates_take_half_each_and_fill_shortage():
    policies = {
        no: PolicyRecord(
            policy_no=no, policy_name=f"정책 {no}", application_period_type_code=ALWAYS_OPEN_CODE,
            application_start_date=None, application_end_date=None,
            application_url=None, reference_url_1=None, reference_url_2=None, supervising_org_name=None,
        )
        for no in ["L1", "L2", "L3", "L4", "N1", "N2", "N3", "N4"]
    }
    local = [PolicyGroup(no, 0.9 - i * 0.01, []) for i, no in enumerate(["L1", "L2", "L3", "L4"])]
    national = [PolicyGroup(no, 0.95 - i * 0.01, []) for i, no in enumerate(["N1", "N2", "N3", "N4"])]

    even = pick_split_candidates(local, national, policies, TODAY, limit=6)
    # 지역 정책이 하나뿐이면 나머지는 전국 정책으로 채움
    short = pick_split_candidates(local[:1], national, policies, TODAY, limit=6)

    assert {c.policy_no for c in even} == {"L1", "L2", "L3", "N1", "N2", "N3"}
    assert {c.policy_no for c in short} == {"L1", "N1", "N2", "N3", "N4"}
