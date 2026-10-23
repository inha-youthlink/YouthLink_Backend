from enum import StrEnum

from pydantic import BaseModel


class Intent(StrEnum):
    POLICY_LIST = "policy_list"
    POLICY_DETAIL = "policy_detail"
    ELIGIBILITY = "eligibility"
    COMPARISON = "comparison"
    CHITCHAT = "chitchat"
    OUT_OF_SCOPE = "out_of_scope"
    UNCLEAR = "unclear"


class StatusFilter(StrEnum):
    NONE = "none"
    OPEN_ONLY = "open_only"
    INCLUDE_CLOSED = "include_closed"


# 구조화 출력은 모든 필드를 필수로 요구해 기본값을 두지 않고, 개수 제한은 출력 검증에서 확인함
class SubQuery(BaseModel):
    # LLM은 앞 필드부터 생성하므로 맥락 해소(독립 질문, 대상)를 분류보다 앞에 둠
    standalone_question: str
    # 긴 policy_no를 잘못 옮겨 쓰는 오류를 피하려고 프롬프트 후보 표기("2", "C1")를 받음
    target_refs: list[str]
    intent: Intent
    status_filter: StatusFilter


class QueryAnalysis(BaseModel):
    # 단일 → 목록 전환 시 프롬프트, 검증, trace를 모두 고쳐야 해서 처음부터 목록으로 둠
    sub_queries: list[SubQuery]
    is_new_topic: bool
