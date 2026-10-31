from typing import Annotated

from pydantic import BeforeValidator

from app.schemas.public import PublicModel
from app.schemas.session import PolicyCategory, PolicySummary

# 정책 검색 상한 (계약 4.4). 문장이 아니라 낱말로 찾는 검색이라 검색어를 짧게 둠
KEYWORD_MAX_CHARS = 50
PAGE_SIZE_MAX = 50

# 과도기 호환: v0.3 프론트의 FINANCE는 데이터에서 복지와 한 묶음이라 WELFARE로 받음. 프론트가 바뀌면 삭제
LEGACY_SEARCH_CATEGORIES = {"FINANCE": PolicyCategory.WELFARE}


# 빈 값(category=)은 검색어와 같이 조건 없음으로 봄
def _search_category(value: object) -> object:
    if value == "":
        return None
    return LEGACY_SEARCH_CATEGORIES.get(value, value) if isinstance(value, str) else value


SearchCategory = Annotated[PolicyCategory | None, BeforeValidator(_search_category)]


class RegionOption(PublicModel):
    code: str
    name: str


# 지역 목록 (계약 B2). 시군구 첫 항목은 "○○ 전체"(○○000)
class SidoRegions(PublicModel):
    code: str
    name: str
    sigungu: list[RegionOption]


# 정책 상세 (계약 4.3, B7). 문장은 서비스가 정책 원문으로 만들고, 비어 있으면 확인할 곳을 안내하는 문구를 넣음
class PolicyDetail(PolicySummary):
    summary: str
    eligibility: str
    benefits: str
    how_to_apply: str
    # 원문이 자유 문장이라 목록이 아니라 문자열 하나
    required_documents: str
