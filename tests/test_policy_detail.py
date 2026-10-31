from datetime import date

import pytest

from app.repositories.policy_repository import PolicyDetailRecord, PolicyRecord
from app.services.policy_detail import eligibility_text, missing_text

RECORD = PolicyRecord(
    policy_no="P-1",
    policy_name="청년 정책",
    application_period_type_code=None,
    application_start_date=date(2026, 1, 1),
    application_end_date=None,
    application_url=None,
    reference_url_1=None,
    reference_url_2=None,
    supervising_org_name=None,
)


def _detail(**columns) -> PolicyDetailRecord:
    defaults = dict.fromkeys((
        "description", "support_content", "application_method", "submission_documents",
        "additional_qualification", "participation_exclusion", "operating_org_name", "min_age", "max_age",
        "income_condition_code", "min_income", "max_income", "income_etc", "marriage_name",
    ))
    return PolicyDetailRecord(record=RECORD, **{**defaults, **columns})


def _first_line(**columns) -> str:
    return eligibility_text(_detail(**columns)).splitlines()[0]


@pytest.mark.parametrize(("min_age", "max_age", "text"), [
    (19, 39, "만 19~39세"),
    (19, None, "만 19세 이상"),
    (None, 34, "만 34세 이하"),
    # 1세 이하·99세 이상 끝은 사실상 제한 없음
    (1, 39, "만 39세 이하"),
    (19, 99, "만 19세 이상"),
    (1, 99, "제한 없음"),
    (0, 120, "제한 없음"),
    (None, None, "제한 없음"),
])
def test_age(min_age, max_age, text):
    assert _first_line(min_age=min_age, max_age=max_age) == f"- 나이: {text}"


@pytest.mark.parametrize(("columns", "text"), [
    ({"income_condition_code": "0043001"}, "무관"),
    ({"income_condition_code": "0043002", "min_income": 0, "max_income": 5000}, "연소득 5,000만 원 이하"),
    ({"income_condition_code": "0043002", "min_income": 2000, "max_income": 5000}, "연소득 2,000만 원 이상 5,000만 원 이하"),
    # 원 단위로 잘못 들어간 상한은 숫자를 쓰지 않음
    ({"income_condition_code": "0043002", "max_income": 43_056_240}, "연소득 기준 있음 (공고 확인)"),
    ({"income_condition_code": "0043002", "max_income": None}, "연소득 기준 있음 (공고 확인)"),
    ({"income_condition_code": "0043003", "income_etc": " 기준 중위소득 150% 이하\n"}, "기준 중위소득 150% 이하"),
    ({"income_condition_code": "0043003", "income_etc": None}, "기준 있음 (공고 확인)"),
])
def test_income(columns, text):
    assert eligibility_text(_detail(**columns)).splitlines()[1] == f"- 소득: {text}"


def test_eligibility_full():
    detail = _detail(
        min_age=19, max_age=39, income_condition_code="0043001", marriage_name="미혼",
        eligibility_names={
            "SPECIAL": ["중소기업"],
            "JOB": ["미취업자", "프리랜서"],
            "MAJOR": ["제한없음"],
            "SCHOOL": ["대학 졸업"],
        },
        additional_qualification="  - 신규 영농경력 3년 이내\n- 전업할 자 ",
        participation_exclusion="농대 재학생",
    )

    assert eligibility_text(detail) == "\n".join([
        "- 나이: 만 19~39세",
        "- 소득: 무관",
        "- 혼인: 미혼",
        "- 취업 상태: 미취업자, 프리랜서",
        "- 학력: 대학 졸업",
        # 제한없음뿐인 전공은 뺌
        "- 특화 대상: 중소기업",
        "추가 자격",
        "- 신규 영농경력 3년 이내\n- 전업할 자",
        "참여 제한 대상",
        "농대 재학생",
    ])


def test_eligibility_other_only_when_mixed():
    detail = _detail(eligibility_names={
        "SCHOOL": ["대졸 예정", "대학 졸업", "기타"],
        "MAJOR": ["기타"],
        "SPECIAL": ["제한없음", "기타"],
    })

    assert eligibility_text(detail).splitlines()[1:] == ["- 학력: 대졸 예정, 대학 졸업, 기타"]


def test_eligibility_skips_empty_and_unrestricted():
    # 소득 코드 없음, 혼인 제한없음, 공백만 있는 원문
    detail = _detail(marriage_name="제한없음", additional_qualification="  \n", participation_exclusion="")

    assert eligibility_text(detail) == "- 나이: 제한 없음"


@pytest.mark.parametrize(("source_url", "organization", "text"), [
    ("https://a.kr", "서울특별시", "공고에 나와 있지 않아요. 담당 기관(서울특별시)에 문의하거나 관련 사이트를 참고해 주세요."),
    (None, " 서울특별시 ", "공고에 나와 있지 않아요. 담당 기관(서울특별시)에 문의해 주세요."),
    ("https://a.kr", None, "공고에 나와 있지 않아요. 관련 사이트를 참고하거나 정책을 운영하는 기관에 문의해 주세요."),
    (None, "  ", "공고에 나와 있지 않아요. 정책을 운영하는 기관에 문의해 주세요."),
])
def test_missing_text(source_url, organization, text):
    assert missing_text(source_url, organization, None) == text


@pytest.mark.parametrize(("organization", "operator", "contact"), [
    # 다르면 함께
    ("고용노동부", " 한국산업인력공단 ", "고용노동부, 운영: 한국산업인력공단"),
    ("통영시", "기획예산실", "통영시, 운영: 기획예산실"),
    # 같거나 비면 하나만
    ("서울특별시", "서울특별시", "서울특별시"),
    ("서울특별시", "  ", "서울특별시"),
    (None, "한국장학재단", "한국장학재단"),
])
def test_missing_text_contact(organization, operator, contact):
    assert missing_text(None, organization, operator) == f"공고에 나와 있지 않아요. 담당 기관({contact})에 문의해 주세요."
