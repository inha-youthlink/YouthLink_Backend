from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from app.schemas.analysis import Category
from app.schemas.chat import QUESTION_MAX_CHARS
from app.schemas.pipeline import PolicyItem
from app.schemas.policy_info import ApplicationStatus
from app.schemas.public import KstDateTime, PublicModel

# 프로필 enum 이름과 값은 계약 v0.3 §2.3을 따름. DB 공통코드와의 대응은 services/profile_codes.py


class MaritalStatus(StrEnum):
    SINGLE = "SINGLE"
    MARRIED = "MARRIED"


class Education(StrEnum):
    BELOW_HIGH_SCHOOL = "BELOW_HIGH_SCHOOL"
    HIGH_SCHOOL_ENROLLED = "HIGH_SCHOOL_ENROLLED"
    HIGH_SCHOOL_GRADUATED = "HIGH_SCHOOL_GRADUATED"
    COLLEGE_ENROLLED = "COLLEGE_ENROLLED"
    COLLEGE_EXPECTED = "COLLEGE_EXPECTED"
    COLLEGE_GRADUATED = "COLLEGE_GRADUATED"
    GRADUATE_SCHOOL = "GRADUATE_SCHOOL"
    OTHER = "OTHER"


class Major(StrEnum):
    HUMANITIES = "HUMANITIES"
    SOCIAL = "SOCIAL"
    BUSINESS = "BUSINESS"
    NATURAL_SCIENCE = "NATURAL_SCIENCE"
    ENGINEERING = "ENGINEERING"
    ARTS = "ARTS"
    AGRICULTURE = "AGRICULTURE"
    OTHER = "OTHER"
    NONE = "NONE"


class EmploymentStatus(StrEnum):
    EMPLOYED = "EMPLOYED"
    SELF_EMPLOYED = "SELF_EMPLOYED"
    UNEMPLOYED = "UNEMPLOYED"
    FREELANCER = "FREELANCER"
    DAILY_WORKER = "DAILY_WORKER"
    STARTUP = "STARTUP"
    SHORT_TERM_WORKER = "SHORT_TERM_WORKER"
    FARMER = "FARMER"
    OTHER = "OTHER"


class SpecialField(StrEnum):
    SME = "SME"
    WOMEN = "WOMEN"
    BASIC_LIVELIHOOD = "BASIC_LIVELIHOOD"
    SINGLE_PARENT = "SINGLE_PARENT"
    DISABLED = "DISABLED"
    FARMER = "FARMER"
    SOLDIER = "SOLDIER"
    LOCAL_TALENT = "LOCAL_TALENT"
    NONE = "NONE"


# 정책 데이터 대분류와 1:1 (계약 H1). 데이터에서 금융과 복지가 나뉘지 않아 FINANCE는 없음
class PolicyCategory(StrEnum):
    JOB = "JOB"
    HOUSING = "HOUSING"
    EDUCATION = "EDUCATION"
    WELFARE = "WELFARE"
    PARTICIPATION = "PARTICIPATION"


POLICY_CATEGORIES = {
    Category.JOB: PolicyCategory.JOB,
    Category.HOUSING: PolicyCategory.HOUSING,
    Category.EDUCATION: PolicyCategory.EDUCATION,
    Category.WELFARE_CULTURE: PolicyCategory.WELFARE,
    Category.PARTICIPATION: PolicyCategory.PARTICIPATION,
}

# 과도기 호환: 프론트가 regionLabel로 바꾸기 전까지 v0.3 카드의 region(시도 enum)을 함께 보냄. 바뀌면 삭제
# 광주·전남은 통합특별시 하나라 v0.3 값 중 하나로만 보낼 수 있음
LEGACY_CARD_REGIONS = {
    "서울특별시": "SEOUL",
    "부산광역시": "BUSAN",
    "대구광역시": "DAEGU",
    "인천광역시": "INCHEON",
    "전남광주통합특별시": "JEONNAM",
    "대전광역시": "DAEJEON",
    "울산광역시": "ULSAN",
    "세종특별자치시": "SEJONG",
    "경기도": "GYEONGGI",
    "강원특별자치도": "GANGWON",
    "충청북도": "CHUNGBUK",
    "충청남도": "CHUNGNAM",
    "전북특별자치도": "JEONBUK",
    "경상북도": "GYEONGBUK",
    "경상남도": "GYEONGNAM",
    "제주특별자치도": "JEJU",
}
NATIONWIDE = "NATIONWIDE"


# 계약상 8항목 모두 필수. region은 5자리 지역 코드이고, 형식·존재 확인은 지역 표가 필요해 서비스에서 함
class SessionProfileRequest(PublicModel):
    region: str = Field(min_length=1, max_length=20)
    marital_status: MaritalStatus
    age: int = Field(ge=15, le=39)
    # 만 원 단위. 원으로 바꿔도 DB BIGINT를 넘지 않게 상한을 둠 (100억 원)
    annual_income: int = Field(ge=0, le=1_000_000)
    education: Education
    major: Major
    employment_status: EmploymentStatus
    special_field: SpecialField


# 응답은 계약 형식으로 되돌려 줌. 8주차 임시 형식으로 만든 세션은 값이 비어 있을 수 있어 null을 허용함
class SessionProfile(PublicModel):
    region: str | None
    marital_status: MaritalStatus | None
    age: int | None
    annual_income: int | None
    education: Education | None
    major: Major | None
    employment_status: EmploymentStatus | None
    special_field: SpecialField | None


# 세션 관심 분야(category)는 받지 않음 (계약 B3). v0.3 프론트가 보내도 모르는 필드라 무시됨
class CreateSessionRequest(PublicModel):
    profile: SessionProfileRequest


class ChatSession(PublicModel):
    id: UUID
    title: str
    profile: SessionProfile
    created_at: KstDateTime


# 이름은 계약(PolicySummary, B5~B7)을 따름
class PolicySummary(PublicModel):
    id: str
    title: str
    category: PolicyCategory | None
    apply_start: date | None
    apply_end: date | None
    status: ApplicationStatus | None
    region_label: str | None
    # 과도기 호환 (LEGACY_CARD_REGIONS)
    region: str
    organization: str | None
    source_url: str | None

    @classmethod
    def from_item(cls, item: PolicyItem) -> "PolicySummary":
        return cls(
            id=item.policy_no,
            title=item.policy_name,
            category=POLICY_CATEGORIES.get(item.category),
            apply_start=item.application_start_date,
            apply_end=item.application_end_date,
            status=item.application_status,
            region_label=item.region_label,
            region=legacy_card_region(item.region_label),
            organization=item.supervising_org_name,
            source_url=item.application_url,
        )


# "서울특별시 강남구", "경기도 5곳"은 그 시도, "전국"·"서울특별시 외 14개 시도"는 시도 하나로 나타낼 수 없어 전국
def legacy_card_region(region_label: str | None) -> str:
    if not region_label or " 외 " in region_label:
        return NATIONWIDE
    return LEGACY_CARD_REGIONS.get(region_label.split()[0], NATIONWIDE)


class Message(PublicModel):
    id: str
    # 질문에만 있음 (계약 R6). 프론트가 먼저 그린 말풍선과 저장된 이력을 맞추는 데 씀
    client_message_id: str | None = None
    role: Literal["user", "assistant"]
    content: str
    policies: list[PolicySummary] | None
    created_at: KstDateTime


class ChatRequest(PublicModel):
    # 재시도 때 같은 값을 보내는 멱등 키 (계약 §5.3)
    client_message_id: UUID
    # 공백만 있는 질문은 LLM을 불러도 답할 수 없어 앞뒤 공백을 지운 뒤 길이를 봄. 저장·재시도 비교도 지운 값으로 함
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUESTION_MAX_CHARS)]
