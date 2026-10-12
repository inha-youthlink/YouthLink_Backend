import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from openai import OpenAIError
from sqlalchemy.exc import SQLAlchemyError

from app.core.dependencies import PipelineDeps, get_pipeline_deps
from app.pipeline.runner import run_pipeline
from app.schemas.pipeline import PUBLIC_TRACE_KEYS, PipelineInput, PipelineOutput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/pipeline", response_model=PipelineOutput)
async def pipeline(
    inp: PipelineInput,
    debug: bool = Query(default=False, description="true면 trace에 근거 블록과 검색된 청크를 포함 (평가·디버깅용)"),
    deps: PipelineDeps = Depends(get_pipeline_deps),
) -> PipelineOutput:
    # 외부 서비스 장애는 원인만 구분해 503으로 알리고, 상세 내용은 서버 로그에만 남김
    try:
        output = await run_pipeline(inp, deps)
    except OpenAIError:
        logger.exception("OpenAI 호출 실패")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM 서비스 호출에 실패했습니다.")
    except (SQLAlchemyError, OSError):
        logger.exception("DB 호출 실패")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "데이터베이스 호출에 실패했습니다.")

    if not debug:
        output.trace = {key: value for key, value in output.trace.items() if key in PUBLIC_TRACE_KEYS}
    return output
