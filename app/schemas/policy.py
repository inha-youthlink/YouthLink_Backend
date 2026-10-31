from typing import Annotated

from pydantic import BeforeValidator

from app.schemas.public import PublicModel
from app.schemas.session import PolicyCategory

# 정책 검색 상한 (계약 4.4). 문장이 아니라 낱말로 찾는 검색이라 검색어를 짧게 둠
KEYWORD_MAX_CHARS = 50
PAGE_SIZE_MAX = 50

# 과도기 호환: v0.3 프론트의 FINANCE는 데이터에서 복지와 한 묶음이라 WELFARE로 받음. 프론트가 바뀌면 삭제
LEGACY_SEARCH_CATEGORIES = {"FINANCE": PolicyCategory.WELFARE}
SearchCategory = Annotated[PolicyCategory, BeforeValidator(lambda value: LEGACY_SEARCH_CATEGORIES.get(value, value))]


class RegionOption(PublicModel):
    code: str
    name: str


# 지역 목록 (계약 B2). 시군구 첫 항목은 "○○ 전체"(○○000)
class SidoRegions(PublicModel):
    code: str
    name: str
    sigungu: list[RegionOption]
