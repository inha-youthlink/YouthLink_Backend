from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from app.core.dependencies import get_guide_store
from app.core.errors import GuideNotFoundError
from app.schemas.public import ErrorResponse
from app.services.guide_store import GuideStore

router = APIRouter(prefix="/api/guides", tags=["guides"])

# 브라우저가 pdfUrl을 바로 여는 주소라 X-Client-Id를 받지 않음 (계약 4.7). 토큰이 열쇠 역할
PDF_PATH = "/{token}.pdf"


@router.get(PDF_PATH, response_class=Response, responses={
    status.HTTP_200_OK: {"content": {"application/pdf": {}}},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
})
async def get_guide_pdf(token: str, store: Annotated[GuideStore, Depends(get_guide_store)]) -> Response:
    guide = await store.load(token)
    if guide is None:
        raise GuideNotFoundError
    return Response(
        content=guide.content,
        media_type="application/pdf",
        headers={
            # 한글 파일 이름은 filename*로, 옛 브라우저용 filename은 ASCII로 둠
            "Content-Disposition": f"inline; filename=\"guide.pdf\"; filename*=UTF-8''{quote(guide.filename)}",
            # 만료된 뒤에도 캐시에서 열리지 않게 함
            "Cache-Control": "no-store",
            # 공통 CSP(default-src 'none')는 브라우저 PDF 뷰어를 막을 수 있어 PDF에는 다른 사이트에 끼워지는 것만 막음
            "Content-Security-Policy": "frame-ancestors 'none'",
        },
    )
