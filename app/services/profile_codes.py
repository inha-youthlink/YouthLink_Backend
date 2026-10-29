import re

from app.core.errors import ValidationFailedError
from app.pipeline.conditions import RegionIndex
from app.schemas.pipeline import Profile
from app.schemas.session import (
    Education,
    EmploymentStatus,
    Major,
    MaritalStatus,
    SessionProfile,
    SessionProfileRequest,
    SpecialField,
)

# 계약 enum ↔ 정책 데이터 공통코드 (계약 4.2 H2)
# NONE(해당 없음)은 "제한없음" 코드라 특정 대상 전용 정책이 빠지고, OTHER(기타)는 "기타" 코드라 1차 필터가 거르지 않음
MARITAL_CODES = {
    MaritalStatus.SINGLE: "0055002",
    MaritalStatus.MARRIED: "0055001",
}
# 고졸 예정(0049003)은 계약 enum에 없음
EDUCATION_CODES = {
    Education.BELOW_HIGH_SCHOOL: "0049001",
    Education.HIGH_SCHOOL_ENROLLED: "0049002",
    Education.HIGH_SCHOOL_GRADUATED: "0049004",
    Education.COLLEGE_ENROLLED: "0049005",
    Education.COLLEGE_EXPECTED: "0049006",
    Education.COLLEGE_GRADUATED: "0049007",
    Education.GRADUATE_SCHOOL: "0049008",
    Education.OTHER: "0049009",
}
MAJOR_CODES = {
    Major.HUMANITIES: "0011001",
    Major.SOCIAL: "0011002",
    Major.BUSINESS: "0011003",
    Major.NATURAL_SCIENCE: "0011004",
    Major.ENGINEERING: "0011005",
    Major.ARTS: "0011006",
    Major.AGRICULTURE: "0011007",
    Major.OTHER: "0011008",
    Major.NONE: "0011009",
}
EMPLOYMENT_CODES = {
    EmploymentStatus.EMPLOYED: "0013001",
    EmploymentStatus.SELF_EMPLOYED: "0013002",
    EmploymentStatus.UNEMPLOYED: "0013003",
    EmploymentStatus.FREELANCER: "0013004",
    EmploymentStatus.DAILY_WORKER: "0013005",
    EmploymentStatus.STARTUP: "0013006",
    EmploymentStatus.SHORT_TERM_WORKER: "0013007",
    EmploymentStatus.FARMER: "0013008",
    EmploymentStatus.OTHER: "0013009",
}
SPECIAL_CODES = {
    SpecialField.SME: "0014001",
    SpecialField.WOMEN: "0014002",
    SpecialField.BASIC_LIVELIHOOD: "0014003",
    SpecialField.SINGLE_PARENT: "0014004",
    SpecialField.DISABLED: "0014005",
    SpecialField.FARMER: "0014006",
    SpecialField.SOLDIER: "0014007",
    SpecialField.LOCAL_TALENT: "0014008",
    SpecialField.NONE: "0014010",
}

# 과도기 호환: v0.3 프론트가 보내는 시도 enum을 "○○ 전체" 코드로 받음. 프론트가 5자리 코드로 바꾸면 삭제
# 광주·전남은 통합특별시(12) 하나로 합쳐짐
LEGACY_SIDO_CODES = {
    "SEOUL": "11",
    "BUSAN": "26",
    "DAEGU": "27",
    "INCHEON": "28",
    "GWANGJU": "12",
    "DAEJEON": "30",
    "ULSAN": "31",
    "SEJONG": "36",
    "GYEONGGI": "41",
    "GANGWON": "51",
    "CHUNGBUK": "43",
    "CHUNGNAM": "44",
    "JEONBUK": "52",
    "JEONNAM": "12",
    "GYEONGBUK": "47",
    "GYEONGNAM": "48",
    "JEJU": "50",
}

REGION_CODE = re.compile(r"\d{5}")
MANWON = 10_000


def to_profile(request: SessionProfileRequest, regions: RegionIndex) -> Profile:
    return Profile(
        age=request.age,
        region_code=region_code(request.region, regions),
        marriage_status_code=MARITAL_CODES[request.marital_status],
        annual_income=request.annual_income * MANWON,
        major_code=MAJOR_CODES[request.major],
        job_code=EMPLOYMENT_CODES[request.employment_status],
        school_code=EDUCATION_CODES[request.education],
        special_target_code=SPECIAL_CODES[request.special_field],
    )


# 모르는 코드는 필터가 조용히 시도 전체로 바꿔 사용자가 알 수 없으므로 세션을 만들 때 막음
# 시도 enum도 행정구역이 바뀌면 표에 없는 코드가 될 수 있어 바꾼 뒤 같은 확인을 거침
def region_code(value: str, regions: RegionIndex) -> str:
    code = LEGACY_SIDO_CODES[value] + "000" if value in LEGACY_SIDO_CODES else value
    if REGION_CODE.fullmatch(code) and regions.is_profile_code(code):
        return code
    raise ValidationFailedError("profile.region", "지원하지 않는 지역 코드입니다.")


def to_session_profile(profile: Profile) -> SessionProfile:
    return SessionProfile(
        region=profile.region_code,
        marital_status=_reverse(MARITAL_CODES, profile.marriage_status_code),
        age=profile.age,
        # 원 단위로 저장해 만 원 단위로 되돌림. 8주차 임시 형식으로 넣은 원 단위 값은 만 원 아래를 버림
        annual_income=profile.annual_income // MANWON if profile.annual_income is not None else None,
        education=_reverse(EDUCATION_CODES, profile.school_code),
        major=_reverse(MAJOR_CODES, profile.major_code),
        employment_status=_reverse(EMPLOYMENT_CODES, profile.job_code),
        special_field=_reverse(SPECIAL_CODES, profile.special_target_code),
    )


# 계약 enum에 없는 코드(고졸 예정, 8주차 임시 형식으로 넣은 값)는 null로 보냄
def _reverse[T](codes: dict[T, str], code: str | None) -> T | None:
    return next((key for key, value in codes.items() if value == code), None)
