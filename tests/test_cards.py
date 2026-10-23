from datetime import date

from app.pipeline.cards import build_cards, pick_candidates
from app.pipeline.generator import ALWAYS_OPEN_CODE, CLOSED_CODE
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
    groups = _groups("CLOSED", "UNKNOWN", "OPEN")

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=2)

    assert [c.policy_no for c in candidates] == ["OPEN", "UNKNOWN"]


def test_candidates_skip_policies_without_record():
    groups = _groups("MISSING", "OPEN")

    candidates = pick_candidates(groups, POLICIES, TODAY, limit=5)

    assert [c.policy_no for c in candidates] == ["OPEN"]


def test_cards_follow_selected_numbers():
    candidates = _groups("OPEN", "ALWAYS", "UPCOMING")

    cards = build_cards(candidates, POLICIES, [1, 3])

    assert [card.policy_no for card in cards] == ["OPEN", "UPCOMING"]
    assert cards[0].policy_name == "정책 OPEN"
    assert cards[0].application_end_date == date(2026, 10, 31)
    assert cards[1].score == candidates[2].score


def test_cards_empty_when_nothing_selected():
    assert build_cards(_groups("OPEN", "ALWAYS"), POLICIES, []) == []


def test_cards_fall_back_to_all_candidates():
    cards = build_cards(_groups("OPEN", "ALWAYS"), POLICIES, None)

    assert [card.policy_no for card in cards] == ["OPEN", "ALWAYS"]
