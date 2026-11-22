from typing import Literal

from pydantic import Field

from app.schemas.public import KstDateTime, PublicModel
from app.schemas.session import PolicySummary

# 추천 에이전트 (계약 3.5, 4.8). 채팅 메시지와 따로 저장되고 서로의 기록에 섞이지 않음

# 저장하지 않는 시작 메시지의 id. 저장된 메시지 id(숫자)와 겹치지 않음
START_MESSAGE_ID = "start"


class AgentCard(PublicModel):
    policy: PolicySummary
    # 시작 카드(프로필 기준만)는 null이라 프론트가 에이전트가 고른 카드와 구분함
    reason: str | None


class AgentMessage(PublicModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    question: str | None = None
    # question이 없으면 항상 빈 목록
    options: list[str] = Field(default_factory=list)
    created_at: KstDateTime | None


class AgentState(PublicModel):
    messages: list[AgentMessage]
    cards: list[AgentCard]
