import re
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

ALWAYS_OPEN_CODE = "0057002"
CLOSED_CODE = "0057003"

KST = ZoneInfo("Asia/Seoul")

# URL 칸에 "전화문의", "-" 같은 글자가 들어 있는 경우가 있어 주소 모양일 때만 링크로 씀
DOMAIN_LIKE = re.compile(r"[\w-]+(\.[\w-]+)+(/\S*)?", re.ASCII)


class ApplicationStatus(StrEnum):
    OPEN = "OPEN"
    ALWAYS_OPEN = "ALWAYS_OPEN"
    UPCOMING = "UPCOMING"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


# 서버 시간대가 UTC여도 신청 상태는 한국 날짜 기준으로 판단함
def today_kst() -> date:
    return datetime.now(KST).date()


# 특정기간 코드는 기간이 지나도 마감으로 바뀌지 않아 날짜로 판단함
# 마감 코드는 기간이 남아 있어도 조기 종료로 봄
def application_status(
    period_type_code: str | None, start: date | None, end: date | None, today: date
) -> ApplicationStatus:
    if period_type_code == ALWAYS_OPEN_CODE:
        return ApplicationStatus.ALWAYS_OPEN
    if period_type_code == CLOSED_CODE:
        return ApplicationStatus.CLOSED

    if start is None and end is None:
        return ApplicationStatus.UNKNOWN
    if end is not None and end < today:
        return ApplicationStatus.CLOSED
    if start is not None and start > today:
        return ApplicationStatus.UPCOMING
    return ApplicationStatus.OPEN


def clean_url(value: str | None) -> str | None:
    value = (value or "").strip()
    if re.match(r"https?://", value, re.IGNORECASE):
        return value
    # "www.fbo.or.kr"처럼 scheme이 빠진 주소는 그대로 두면 링크가 열리지 않아 https를 붙임
    if DOMAIN_LIKE.fullmatch(value):
        return f"https://{value}"
    return None
