import pytest

from app.core.errors import ValidationFailedError
from app.schemas.session import (
    Education,
    EmploymentStatus,
    Major,
    MaritalStatus,
    SessionProfile,
    SessionProfileRequest,
    SpecialField,
)
from app.services.profile_codes import (
    EDUCATION_CODES,
    EMPLOYMENT_CODES,
    LEGACY_SIDO_CODES,
    MAJOR_CODES,
    MARITAL_CODES,
    SPECIAL_CODES,
    region_code,
    to_profile,
    to_session_profile,
)
from tests.test_conditions import REGIONS


# 계약 enum 값이 하나도 빠지지 않고, 서로 다른 값이 같은 코드로 겹치지 않음
@pytest.mark.parametrize(
    ("enum", "codes"),
    [
        (MaritalStatus, MARITAL_CODES),
        (Education, EDUCATION_CODES),
        (Major, MAJOR_CODES),
        (EmploymentStatus, EMPLOYMENT_CODES),
        (SpecialField, SPECIAL_CODES),
    ],
)
def test_every_enum_value_has_its_own_code(enum, codes):
    assert set(codes) == set(enum)
    assert len(set(codes.values())) == len(codes)


@pytest.mark.parametrize(
    ("major", "special", "major_code", "special_code"),
    [
        # 해당 없음은 "제한없음", 기타는 "기타" 코드
        (Major.NONE, SpecialField.NONE, "0011009", "0014010"),
        (Major.OTHER, SpecialField.SME, "0011008", "0014001"),
    ],
)
def test_none_and_other_codes(major, special, major_code, special_code):
    profile = to_profile(_request(major=major, special_field=special), REGIONS)

    assert (profile.major_code, profile.special_target_code) == (major_code, special_code)


def test_round_trip():
    request = _request()

    profile = to_profile(request, REGIONS)

    assert profile.annual_income == 25_000_000
    assert profile.gender is None
    assert to_session_profile(profile) == SessionProfile(**request.model_dump())


def test_unknown_code_is_null_in_response():
    profile = to_profile(_request(), REGIONS).model_copy(update={"school_code": "0049003"})

    assert to_session_profile(profile).education is None


# v0.3 시도 enum 17개를 모두 표에 두고, 광주·전남은 통합특별시 하나로
def test_legacy_sido_enum_becomes_sido_wide_code():
    assert len(LEGACY_SIDO_CODES) == 17
    assert region_code("SEOUL", REGIONS) == "11000"
    assert region_code("GWANGJU", REGIONS) == region_code("JEONNAM", REGIONS) == "12000"


# 시도 enum도 지역 표에 없는 시도면 저장하지 않음 (테스트 지역 표에 부산이 없음)
def test_legacy_sido_enum_not_in_region_table():
    with pytest.raises(ValidationFailedError):
        region_code("BUSAN", REGIONS)


def test_unknown_region_code():
    with pytest.raises(ValidationFailedError):
        region_code("41999", REGIONS)


def _request(**fields) -> SessionProfileRequest:
    values = {
        "region": "11680",
        "marital_status": MaritalStatus.MARRIED,
        "age": 31,
        "annual_income": 2500,
        "education": Education.OTHER,
        "major": Major.HUMANITIES,
        "employment_status": EmploymentStatus.OTHER,
        "special_field": SpecialField.WOMEN,
    }
    return SessionProfileRequest(**(values | fields))
