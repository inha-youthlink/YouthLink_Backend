from app.pipeline.analyzer import MAX_SUB_QUERIES, AnalysisResult
from app.schemas.pipeline import PipelineInput, PolicyRef

# 하위 질문마다 대상이 있을 수 있어 하위 질문 최대 개수와 맞춤
MAX_CURRENT_POLICIES = MAX_SUB_QUERIES


def update_current_policies(inp: PipelineInput, analysis: AnalysisResult) -> list[PolicyRef]:
    # 분석이 실패하면 무엇을 말했는지 알 수 없어 대화 상태를 바꾸지 않음
    if analysis.fallback_reason is not None:
        return inp.current_policies

    targets = {policy.policy_no: policy for sub in analysis.sub_queries for policy in sub.targets}
    # 이전 정책을 남기면 이름 없는 다음 질문이 애매해져 교체함
    if targets:
        return list(targets.values())[:MAX_CURRENT_POLICIES]
    if analysis.is_new_topic:
        return []
    return inp.current_policies
