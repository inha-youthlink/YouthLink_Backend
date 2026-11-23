from dataclasses import dataclass
from enum import StrEnum

from app.pipeline.analyzer import AnalysisResult, ResolvedSubQuery
from app.pipeline.conditions import SearchConditions
from app.pipeline.evidence import AnswerKind
from app.schemas.analysis import Intent

# 비교는 두 정책씩만 함. 대상이 많으면 근거가 길어지고 카드도 많아져 되물음
MAX_COMPARE_TARGETS = 2

# 지목한 정책 하나하나의 근거가 필요한 질문이라 검색 없이 그 정책만 씀
TARGETED_INTENTS = frozenset({Intent.POLICY_DETAIL, Intent.ELIGIBILITY, Intent.COMPARISON})


class Handler(StrEnum):
    SEARCH = "search"
    DETAIL = "detail"
    SHORT = "short"
    CLARIFY = "clarify"
    COMPARE = "compare"
    COMPOSITE = "composite"


# 복합 질문의 하위 질문 하나를 어떻게 다룰지. 근거를 모으는 둘은 검색·상세 핸들러와 같은 방법을 씀
class SubKind(StrEnum):
    SEARCH = "search"
    TARGET = "target"
    # 근거 없이 이유만 안내함. 비교는 대상별 카드 규칙이 복합 카드 규칙과 겹쳐 단일 질문으로 따로 받음
    OUT_OF_SCOPE = "out_of_scope"
    UNCLEAR = "unclear"
    COMPARE = "compare"
    # 인사는 질문 목록에서 뺌
    IGNORE = "ignore"


@dataclass(frozen=True)
class Route:
    handler: Handler
    answer_kind: AnswerKind


def choose_route(analysis: AnalysisResult, conditions: SearchConditions) -> Route:
    route = _route_by_analysis(analysis)
    # 검색은 프로필로 거르므로 질문의 지역·나이가 다르면 말한 조건과 다른 결과를 보여 주게 되어 되물음
    # 지목한 정책은 거르지 않고 그 정책만 쓰므로 답변에서 자격 차이를 설명함
    if _searches(route, analysis) and conditions.ask_back:
        return Route(Handler.CLARIFY, AnswerKind.CLARIFY)
    return route


def _route_by_analysis(analysis: AnalysisResult) -> Route:
    # 분석 실패는 시스템 문제라 사용자에게 되묻지 않고 원래 질문으로 검색함
    if analysis.fallback_reason is not None or not analysis.sub_queries:
        return Route(Handler.SEARCH, AnswerKind.RECOMMEND)
    if len(analysis.sub_queries) > 1:
        kinds = [sub_kind(sub) for sub in analysis.sub_queries]
        # 인사·범위 밖뿐이면 답할 근거가 없어 단일 질문의 인사·범위 밖과 같게 짧게 답함
        if all(kind in (SubKind.IGNORE, SubKind.OUT_OF_SCOPE) for kind in kinds):
            return Route(Handler.SHORT, AnswerKind.SHORT)
        # 모두 지목한 정책을 묻는 질문은 상세 핸들러가 이미 모든 하위 질문의 대상을 모음
        if all(kind == SubKind.TARGET for kind in kinds):
            return Route(Handler.DETAIL, AnswerKind.DETAIL)
        return Route(Handler.COMPOSITE, AnswerKind.COMPOSITE)

    sub = analysis.sub_queries[0]
    if sub.intent in (Intent.CHITCHAT, Intent.OUT_OF_SCOPE):
        return Route(Handler.SHORT, AnswerKind.SHORT)
    if sub.intent == Intent.UNCLEAR or sub.unresolved_refs:
        return Route(Handler.CLARIFY, AnswerKind.CLARIFY)
    # 대상이 하나뿐인 비교는 비교할 상대가 없어 아래 규칙대로 상세나 검색으로 감
    target_count = len(sub.targets) + len(sub.compare_queries)
    if sub.intent == Intent.COMPARISON and target_count >= 2:
        kind = AnswerKind.COMPARE if target_count <= MAX_COMPARE_TARGETS else AnswerKind.CLARIFY
        return Route(Handler.COMPARE, kind)
    if sub.targets and sub.intent in TARGETED_INTENTS:
        return Route(Handler.DETAIL, AnswerKind.DETAIL)
    # 처음 묻는 정책 이름은 지목할 카드가 없어 검색으로 찾고 상세하게 답함
    if sub.intent == Intent.POLICY_DETAIL:
        return Route(Handler.SEARCH, AnswerKind.DETAIL)
    return Route(Handler.SEARCH, AnswerKind.RECOMMEND)


# 단일 질문의 라우팅 규칙과 같은 기준. 대상이 하나뿐인 비교는 단일 질문처럼 지목은 상세, 이름만은 검색
def sub_kind(sub: ResolvedSubQuery) -> SubKind:
    if sub.intent == Intent.CHITCHAT:
        return SubKind.IGNORE
    if sub.intent == Intent.OUT_OF_SCOPE:
        return SubKind.OUT_OF_SCOPE
    if sub.intent == Intent.UNCLEAR or sub.unresolved_refs:
        return SubKind.UNCLEAR
    if sub.intent == Intent.COMPARISON and len(sub.targets) + len(sub.compare_queries) >= 2:
        return SubKind.COMPARE
    if sub.targets and sub.intent in TARGETED_INTENTS:
        return SubKind.TARGET
    return SubKind.SEARCH


# 비교 핸들러도 지목하지 못한 대상은 프로필로 걸러 검색함. 비교 경로는 하위 질문이 하나일 때만 옴
# 복합 질문은 검색하는 하위 질문이 하나라도 있으면 프로필로 거르므로 같이 되물음
def _searches(route: Route, analysis: AnalysisResult) -> bool:
    if route.handler == Handler.SEARCH:
        return True
    if route.handler == Handler.COMPOSITE:
        return any(sub_kind(sub) == SubKind.SEARCH for sub in analysis.sub_queries)
    return (
        route.handler == Handler.COMPARE
        and route.answer_kind == AnswerKind.COMPARE
        and bool(analysis.sub_queries[0].compare_queries)
    )
