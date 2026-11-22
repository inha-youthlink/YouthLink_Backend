from typing import Annotated

from pydantic import BeforeValidator, StringConstraints

from app.schemas.public import KstDateTime, PublicModel
from app.schemas.session import PolicyCategory, PolicySummary

# 정책 검색 상한 (계약 4.4). 문장이 아니라 낱말로 찾는 검색이라 검색어를 짧게 둠
KEYWORD_MAX_CHARS = 50
PAGE_SIZE_MAX = 50
# policy.policy_no 컬럼 길이
POLICY_ID_MAX_CHARS = 30


# 빈 값(category=)은 검색어와 같이 조건 없음으로 봄
def _search_category(value: object) -> object:
    return None if value == "" else value


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


# 스크랩 (계약 4.6). 카드는 채팅·검색과 같은 PolicySummary
class Scrap(PublicModel):
    policy: PolicySummary
    created_at: KstDateTime


class ScrapRequest(PublicModel):
    # policy.policy_no 컬럼 길이(VARCHAR(30)). 앞뒤 공백은 지움
    policy_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=POLICY_ID_MAX_CHARS)]


# 채팅에서 이야기할 정책 지정 (계약 4.9). 형식 검사는 스크랩·가이드와 같음
class CurrentPolicyRequest(PublicModel):
    policy_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=POLICY_ID_MAX_CHARS)]
