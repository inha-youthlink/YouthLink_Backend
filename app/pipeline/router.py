from dataclasses import dataclass
from enum import StrEnum

from app.pipeline.analyzer import AnalysisResult
from app.pipeline.evidence import AnswerKind
from app.schemas.analysis import Intent

# 지목한 정책 하나하나의 근거가 필요한 질문이라 검색 없이 그 정책만 씀
TARGETED_INTENTS = frozenset({Intent.POLICY_DETAIL, Intent.ELIGIBILITY, Intent.COMPARISON})


class Handler(StrEnum):
    SEARCH = "search"
    DETAIL = "detail"
    SHORT = "short"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class Route:
    handler: Handler
    answer_kind: AnswerKind


def choose_route(analysis: AnalysisResult) -> Route:
    # 분석 실패는 시스템 문제라 사용자에게 되묻지 않고, 복합 질문은 전용 처리 전까지 검색으로 보냄
    if analysis.fallback_reason is not None or len(analysis.sub_queries) != 1:
        return Route(Handler.SEARCH, AnswerKind.RECOMMEND)

    sub = analysis.sub_queries[0]
    if sub.intent in (Intent.CHITCHAT, Intent.OUT_OF_SCOPE):
        return Route(Handler.SHORT, AnswerKind.SHORT)
    if sub.intent == Intent.UNCLEAR or sub.unresolved_refs:
        return Route(Handler.CLARIFY, AnswerKind.CLARIFY)
    if sub.targets and sub.intent in TARGETED_INTENTS:
        return Route(Handler.DETAIL, AnswerKind.DETAIL)
    # 처음 묻는 정책 이름은 지목할 카드가 없어 검색으로 찾고 상세하게 답함
    if sub.intent == Intent.POLICY_DETAIL:
        return Route(Handler.SEARCH, AnswerKind.DETAIL)
    return Route(Handler.SEARCH, AnswerKind.RECOMMEND)
