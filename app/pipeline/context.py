from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.core.dependencies import PipelineDeps
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import PipelineInput

# analyzer가 PipelineContext를 가져다 써서 실행 시점에 가져오면 순환 import가 됨
if TYPE_CHECKING:
    from app.pipeline.analyzer import AnalysisResult

KST = ZoneInfo("Asia/Seoul")


# 서버 시간대가 UTC여도 신청 상태는 한국 날짜 기준으로 판단함
def today_kst() -> date:
    return datetime.now(KST).date()


@dataclass
class PipelineContext:
    input: PipelineInput
    deps: PipelineDeps
    query: str
    today: date = field(default_factory=today_kst)
    analysis: "AnalysisResult | None" = None
    groups: list[PolicyGroup] = field(default_factory=list)
    policies: dict[str, PolicyRecord] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
