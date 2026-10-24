import pytest

from app.pipeline.analyzer import AnalysisResult, FallbackReason, ResolvedSubQuery
from app.pipeline.conditions import Mismatch, SearchConditions
from app.pipeline.evidence import AnswerKind
from app.pipeline.router import Handler, Route, choose_route
from app.schemas.analysis import Intent, StatusFilter
from app.schemas.pipeline import PolicyRef

RENT = PolicyRef(policy_no="P-RENT", policy_name="청년 월세 지원")


def _sub(intent: Intent, targets: list[PolicyRef] | None = None, unresolved: list[str] | None = None) -> ResolvedSubQuery:
    return ResolvedSubQuery(
        standalone_question="q", target_refs=[], targets=targets or [], intent=intent,
        status_filter=StatusFilter.NONE, unresolved_refs=unresolved or [],
    )


def _analysis(*subs: ResolvedSubQuery, failed: bool = False) -> AnalysisResult:
    return AnalysisResult(
        sub_queries=list(subs), is_new_topic=False,
        fallback_reason=FallbackReason.LLM_ERROR if failed else None, prompt_version="analyze_v3", model="m",
    )


SEARCH_RECOMMEND = Route(Handler.SEARCH, AnswerKind.RECOMMEND)
SEARCH_DETAIL = Route(Handler.SEARCH, AnswerKind.DETAIL)
DETAIL = Route(Handler.DETAIL, AnswerKind.DETAIL)
SHORT = Route(Handler.SHORT, AnswerKind.SHORT)
CLARIFY = Route(Handler.CLARIFY, AnswerKind.CLARIFY)


@pytest.mark.parametrize(
    ("analysis", "route"),
    [
        (_analysis(failed=True), SEARCH_RECOMMEND),
        (_analysis(_sub(Intent.POLICY_DETAIL, [RENT]), _sub(Intent.POLICY_LIST)), SEARCH_RECOMMEND),
        (_analysis(_sub(Intent.CHITCHAT)), SHORT),
        (_analysis(_sub(Intent.OUT_OF_SCOPE)), SHORT),
        (_analysis(_sub(Intent.UNCLEAR)), CLARIFY),
        (_analysis(_sub(Intent.POLICY_DETAIL, [RENT])), DETAIL),
        (_analysis(_sub(Intent.ELIGIBILITY, [RENT])), DETAIL),
        (_analysis(_sub(Intent.COMPARISON, [RENT])), DETAIL),
        # 없는 카드 번호를 가리키면 일부가 확인돼도 되물음
        (_analysis(_sub(Intent.POLICY_DETAIL, unresolved=["5"])), CLARIFY),
        (_analysis(_sub(Intent.COMPARISON, [RENT], unresolved=["5"])), CLARIFY),
        (_analysis(_sub(Intent.POLICY_DETAIL)), SEARCH_DETAIL),
        (_analysis(_sub(Intent.POLICY_LIST)), SEARCH_RECOMMEND),
        # 지목한 정책과 비슷한 정책을 찾는 질문은 목록이라 검색함
        (_analysis(_sub(Intent.POLICY_LIST, [RENT])), SEARCH_RECOMMEND),
        (_analysis(_sub(Intent.ELIGIBILITY)), SEARCH_RECOMMEND),
        (_analysis(_sub(Intent.COMPARISON)), SEARCH_RECOMMEND),
    ],
)
def test_choose_route(analysis, route):
    assert choose_route(analysis, SearchConditions()) == route


REGION_MISMATCH = SearchConditions(mismatches=[Mismatch("지역", "인천", "서울특별시")])


@pytest.mark.parametrize(
    ("analysis", "route"),
    [
        (_analysis(_sub(Intent.POLICY_LIST)), CLARIFY),
        # 처음 묻는 정책 이름도 프로필 지역으로 거르면 찾지 못해 되물음
        (_analysis(_sub(Intent.POLICY_DETAIL)), CLARIFY),
        (_analysis(_sub(Intent.POLICY_LIST), _sub(Intent.POLICY_LIST)), CLARIFY),
        # 지목한 정책은 거르지 않아 답변에서 설명함
        (_analysis(_sub(Intent.ELIGIBILITY, [RENT])), DETAIL),
        (_analysis(_sub(Intent.CHITCHAT)), SHORT),
    ],
)
def test_region_or_age_mismatch_clarifies_only_search(analysis, route):
    assert choose_route(analysis, REGION_MISMATCH) == route


def test_job_or_school_mismatch_still_searches():
    conditions = SearchConditions(mismatches=[Mismatch("학력", "석·박사", "대학 졸업")])

    assert choose_route(_analysis(_sub(Intent.POLICY_LIST)), conditions) == SEARCH_RECOMMEND
