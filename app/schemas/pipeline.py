from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Profile(BaseModel):
    age: int | None = Field(default=None, ge=0)
    gender: str | None = None
    region_code: str | None = None
    marriage_status_code: str | None = None
    annual_income: int | None = Field(default=None, ge=0)
    major_code: str | None = None
    job_code: str | None = None
    school_code: str | None = None
    special_target_code: str | None = None


class PolicyRef(BaseModel):
    policy_no: str
    policy_name: str


class ConversationTurn(BaseModel):
    question: str
    # 자르는 길이는 단계마다 달라 전문을 받고 쓰는 쪽에서 자름
    answer: str
    # 목록 순서가 카드 순서이며, 턴마다 두어 후보 범위를 넓혀도 입력 형태가 그대로임
    policies: list[PolicyRef] = Field(default_factory=list)


class PipelineInput(BaseModel):
    question: str = Field(min_length=1)
    profile: Profile = Field(default_factory=Profile)
    # 오래된 턴부터. 몇 턴을 넣을지는 호출 측이 정함
    history: list[ConversationTurn] = Field(default_factory=list)
    current_policies: list[PolicyRef] = Field(default_factory=list)


class Chunk(BaseModel):
    chunk_id: UUID
    policy_no: str
    chunk_index: int
    chunk_type: str
    content: str
    score: float


class PolicyItem(BaseModel):
    policy_no: str
    policy_name: str
    application_start_date: date | None = None
    application_end_date: date | None = None
    application_url: str | None = None
    score: float


class PipelineOutput(BaseModel):
    answer: str
    policies: list[PolicyItem]
    trace: dict[str, Any]
    # 이번 턴이 끝난 뒤의 전체 상태라 바뀌지 않았으면 입력 값이 그대로 들어감
    current_policies: list[PolicyRef] = Field(default_factory=list)


# 근거 원문, 청크 정보 같은 내부 정보가 서비스 응답에 나가지 않도록 허용한 키만 남김
# 허용 목록이라 새 trace 키는 여기 추가하기 전까지 debug 응답에만 포함됨
# 채팅 답변 metadata도 같은 기준을 써서 API 응답과 저장 기록의 trace 범위를 맞춤
PUBLIC_TRACE_KEYS = frozenset({
    "latency_ms",
    "tokens",
    "chat_model",
    "retriever",
    "top_k",
    "prompt_version",
    "embedding_model",
    "handler",
    "analysis",
    "current_policy_nos",
    "cards",
})
