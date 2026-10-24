from app.pipeline.context import PipelineContext
from app.pipeline.evidence import Evidence


# 인사·범위 밖 질문에 정책 근거를 넣으면 관련 없는 정책을 소개하게 되어 근거 없이 답함
async def collect(ctx: PipelineContext) -> Evidence:
    return Evidence(fixed_cards=True)
