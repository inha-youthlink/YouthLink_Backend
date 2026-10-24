from dataclasses import dataclass, field
from enum import StrEnum

from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord

# 지목한 정책이나 되묻기 보기처럼 검색하지 않은 카드는 유사도 점수가 없음
NO_SCORE = 0.0


class AnswerKind(StrEnum):
    RECOMMEND = "recommend"
    DETAIL = "detail"
    SHORT = "short"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class Evidence:
    # 이 순서가 근거 번호이자 카드 순서임
    candidates: list[PolicyGroup] = field(default_factory=list)
    policies: dict[str, PolicyRecord] = field(default_factory=dict)
    documents: dict[str, list[str]] = field(default_factory=dict)
    region_labels: dict[str, str] = field(default_factory=dict)
    # 지목한 정책처럼 카드가 미리 정해지면 LLM에 고르게 하지 않음
    fixed_cards: bool = False
    # 되묻기처럼 LLM 없이 정해지는 답변
    reply: str | None = None
