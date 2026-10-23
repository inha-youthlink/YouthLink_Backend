from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from app.core.dependencies import PipelineDeps
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import PipelineInput, PolicyRef
from app.schemas.policy_info import today_kst

# analyzer가 PipelineContext를 가져다 써서 실행 시점에 가져오면 순환 import가 됨
if TYPE_CHECKING:
    from app.pipeline.analyzer import AnalysisResult

@dataclass
class PipelineContext:
    input: PipelineInput
    deps: PipelineDeps
    query: str
    today: date = field(default_factory=today_kst)
    analysis: "AnalysisResult | None" = None
    current_policies: list[PolicyRef] = field(default_factory=list)
    groups: list[PolicyGroup] = field(default_factory=list)
    policies: dict[str, PolicyRecord] = field(default_factory=dict)
    # 답변 생성에 넘기는 정책. 이 순서가 근거 번호이자 카드 순서임
    candidates: list[PolicyGroup] = field(default_factory=list)
    documents: dict[str, list[str]] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
