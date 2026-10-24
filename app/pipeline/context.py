from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from app.core.dependencies import PipelineDeps
from app.pipeline.evidence import AnswerKind, Evidence
from app.schemas.pipeline import PipelineInput, PolicyRef
from app.schemas.policy_info import today_kst

# analyzer가 PipelineContext를 가져다 써서 실행 시점에 가져오면 순환 import가 됨
if TYPE_CHECKING:
    from app.pipeline.analyzer import AnalysisResult
    from app.pipeline.conditions import RegionIndex, SearchConditions


@dataclass
class PipelineContext:
    input: PipelineInput
    deps: PipelineDeps
    # 답변 생성에 쓰는 독립 질문. 검색은 조건을 뺀 search_query로 함
    query: str
    search_query: str = ""
    today: date = field(default_factory=today_kst)
    analysis: "AnalysisResult | None" = None
    current_policies: list[PolicyRef] = field(default_factory=list)
    answer_kind: AnswerKind = AnswerKind.RECOMMEND
    evidence: Evidence = field(default_factory=Evidence)
    conditions: "SearchConditions | None" = None
    regions: "RegionIndex | None" = None
    code_names: dict[str, str] | None = None
    trace: dict[str, Any] = field(default_factory=dict)
