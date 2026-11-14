from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.schemas.policy import POLICY_ID_MAX_CHARS
from app.schemas.public import KstDateTime, PublicModel

# 신청 가이드 PDF (계약 4.7)


class GuideRequest(PublicModel):
    # policy.policy_no 컬럼 길이(VARCHAR(30)). 앞뒤 공백은 지움
    policy_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=POLICY_ID_MAX_CHARS)]


class GuideResult(PublicModel):
    policy_id: str
    pdf_url: str
    expires_at: KstDateTime
    # 세션 프로필의 1차 필터(채팅 추천과 같은 기준) 밖 정책. 프론트가 PDF를 열 때 안내를 함께 띄움 (PDF 주의사항에도 있음)
    outside_profile: bool = False


# LLM 출력. 구조화 출력은 모든 필드를 필수로 요구해 기본값을 두지 않음
# 날짜·금액·링크·기관명은 받지 않음 (PDF에는 공고 데이터에서 코드가 넣음)
class ObservedFact(BaseModel):
    item: str
    value: str


class GuideStep(BaseModel):
    action: str
    where: str


class GuideTerm(BaseModel):
    term: str
    meaning: str


class GuideDraft(BaseModel):
    # 대화에서 사용자가 직접 말한 사항. 신청 양식 자동 작성에서도 같은 항목을 다시 쓸 예정이라 문장이 아니라 항목으로 받음
    observed: list[ObservedFact]
    # 추가 자격·참여 제한 원문을 항목으로 나눈 것 (숫자 조건은 코드가 만듦)
    conditions: list[str]
    steps: list[GuideStep]
    documents: list[str]
    cautions: list[str]
    terms: list[GuideTerm]
