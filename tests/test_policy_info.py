from datetime import date

import pytest

from app.schemas.policy_info import ALWAYS_OPEN_CODE, CLOSED_CODE, ApplicationStatus, application_status, clean_url

TODAY = date(2026, 10, 23)
PERIOD_CODE = "0057001"


@pytest.mark.parametrize(
    ("code", "start", "end", "expected"),
    [
        (PERIOD_CODE, date(2026, 10, 1), date(2026, 10, 31), ApplicationStatus.OPEN),
        # 마감일 당일까지는 신청 가능
        (PERIOD_CODE, date(2026, 10, 1), TODAY, ApplicationStatus.OPEN),
        (PERIOD_CODE, date(2026, 11, 1), date(2026, 11, 30), ApplicationStatus.UPCOMING),
        (ALWAYS_OPEN_CODE, None, None, ApplicationStatus.ALWAYS_OPEN),
        (CLOSED_CODE, None, None, ApplicationStatus.CLOSED),
        # 마감 코드는 기간이 남아 있어도 조기 종료로 봄
        (CLOSED_CODE, date(2026, 10, 1), date(2026, 12, 31), ApplicationStatus.CLOSED),
        (PERIOD_CODE, date(2026, 9, 1), date(2026, 10, 22), ApplicationStatus.CLOSED),
        (PERIOD_CODE, None, None, ApplicationStatus.UNKNOWN),
        (None, None, None, ApplicationStatus.UNKNOWN),
    ],
)
def test_application_status(code, start, end, expected):
    assert application_status(code, start, end, TODAY) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.myhome.go.kr/hws/portal/main", "https://www.myhome.go.kr/hws/portal/main"),
        ("  http://www.saca.or.kr/  ", "http://www.saca.or.kr/"),
        ("www.fbo.or.kr", "https://www.fbo.or.kr"),
        ("www.khug.or.kr/jeonse/index.js", "https://www.khug.or.kr/jeonse/index.js"),
        ("전화문의", None),
        ("-", None),
        ("", None),
        (None, None),
    ],
)
def test_clean_url(value, expected):
    assert clean_url(value) == expected
