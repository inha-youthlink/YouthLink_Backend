from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.chat import QUESTION_MAX_CHARS
from app.schemas.pipeline import PolicyItem, Profile
from app.schemas.policy_info import ApplicationStatus
from app.schemas.public import KstDateTime, PublicModel


# 프로필 enum 합의(계약 H2·H3) 전이라 /internal과 같은 DB 코드 Profile을 그대로 받음
class CreateSessionRequest(PublicModel):
    profile: Profile = Field(default_factory=Profile)


class ChatSession(PublicModel):
    id: UUID
    title: str
    profile: Profile
    created_at: KstDateTime


# 이름은 계약(PolicySummary, B5~B7)을 따름. category는 코드 합의(H1) 전이라 아직 없음
class PolicySummary(PublicModel):
    id: str
    title: str
    apply_start: date | None
    apply_end: date | None
    status: ApplicationStatus | None
    region_label: str | None
    organization: str | None
    source_url: str | None

    @classmethod
    def from_item(cls, item: PolicyItem) -> "PolicySummary":
        return cls(
            id=item.policy_no,
            title=item.policy_name,
            apply_start=item.application_start_date,
            apply_end=item.application_end_date,
            status=item.application_status,
            region_label=item.region_label,
            organization=item.supervising_org_name,
            source_url=item.application_url,
        )


class Message(PublicModel):
    id: str
    # 멱등 처리(계약 R6)에서 저장하기 전까지는 항상 null
    client_message_id: str | None = None
    role: Literal["user", "assistant"]
    content: str
    policies: list[PolicySummary] | None
    created_at: KstDateTime


class ChatRequest(PublicModel):
    # 재시도 때 같은 값을 보내는 멱등 키 (계약 §5.3). 뼈대는 형식만 검사함
    client_message_id: UUID
    content: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
