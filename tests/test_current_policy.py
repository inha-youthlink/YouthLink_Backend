from app.pipeline.analyzer import AnalysisResult, FallbackReason, ResolvedSubQuery
from app.pipeline.current_policy import update_current_policies
from app.schemas.analysis import Intent, StatusFilter
from app.schemas.pipeline import ConversationTurn, PipelineInput, PolicyRef

RENT = PolicyRef(policy_no="P-RENT", policy_name="청년 월세 지원")
MOVING = PolicyRef(policy_no="P-MOVE", policy_name="청년 이사비 지원")
CENTER = PolicyRef(policy_no="P-CENTER", policy_name="청년 주거 상담 센터")
LOAN = PolicyRef(policy_no="P-LOAN", policy_name="청년 전세대출")


def _input(current: list[PolicyRef], shown: list[PolicyRef] = (RENT, MOVING, CENTER)) -> PipelineInput:
    return PipelineInput(
        question="질문",
        history=[ConversationTurn(question="q", answer="a", policies=list(shown))],
        current_policies=current,
    )


def _analysis(*targets: list[PolicyRef], intent=Intent.POLICY_DETAIL, is_new_topic=False) -> AnalysisResult:
    subs = [
        ResolvedSubQuery(
            standalone_question="질문", target_refs=[], targets=list(t),
            intent=intent, status_filter=StatusFilter.NONE,
        )
        for t in targets or [[]]
    ]
    return AnalysisResult(
        sub_queries=subs, is_new_topic=is_new_topic, fallback_reason=None,
        prompt_version="analyze_v2", model="m",
    )


def _nos(policies: list[PolicyRef]) -> list[str]:
    return [policy.policy_no for policy in policies]


def test_target_sets_current_policy_with_name():
    result = update_current_policies(_input([]), _analysis([MOVING]))

    assert result == [MOVING]


def test_targets_replace_previous_current_policies():
    result = update_current_policies(_input([LOAN]), _analysis([MOVING], [RENT, MOVING]))

    # 하위 질문의 대상을 등장 순서대로 모으고 중복은 한 번만
    assert _nos(result) == [MOVING.policy_no, RENT.policy_no]


# 화면 밖 현재 정책(C 표기)을 지목해도 analyzer가 넘긴 정책이 그대로 남아야 함
def test_target_from_off_screen_current_policy():
    result = update_current_policies(_input([LOAN]), _analysis([LOAN]))

    assert result == [LOAN]


def test_current_policies_limited():
    shown = [RENT, MOVING, CENTER, LOAN]
    result = update_current_policies(_input([], shown), _analysis(shown))

    assert len(result) == 3


def test_no_target_keeps_current_policies():
    for intent in (Intent.POLICY_LIST, Intent.CHITCHAT, Intent.OUT_OF_SCOPE, Intent.UNCLEAR):
        assert update_current_policies(_input([MOVING]), _analysis(intent=intent)) == [MOVING]


def test_new_topic_clears_current_policies():
    result = update_current_policies(_input([MOVING]), _analysis(intent=Intent.POLICY_LIST, is_new_topic=True))

    assert result == []


# 화제를 바꾸면서 새 목록의 정책을 바로 지목하면 지목한 정책이 남아야 함
def test_new_topic_with_target_sets_target():
    result = update_current_policies(_input([LOAN]), _analysis([RENT], is_new_topic=True))

    assert result == [RENT]


def test_failed_analysis_keeps_current_policies():
    failed = AnalysisResult(
        sub_queries=[], is_new_topic=False, fallback_reason=FallbackReason.LLM_ERROR,
        prompt_version="analyze_v2", model="m",
    )

    assert update_current_policies(_input([MOVING]), failed) == [MOVING]
