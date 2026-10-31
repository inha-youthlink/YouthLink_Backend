from app.repositories.policy_repository import (
    ANNUAL_INCOME_CODE,
    MAX_INCOME_MANWON,
    NO_INCOME_LIMIT_CODE,
    OTHER_INCOME_CODE,
    PolicyDetailRecord,
)

# 1세 이하·99세 이상은 사실상 나이 제한이 없는 값이라 그쪽 끝은 쓰지 않음
MIN_AGE_LIMIT = 1
MAX_AGE_LIMIT = 99
NO_RESTRICTION_NAME = "제한없음"
OTHER_NAME = "기타"
MARRIAGE_NAMES = frozenset({"기혼", "미혼"})
ELIGIBILITY_LABELS = (("JOB", "취업 상태"), ("SCHOOL", "학력"), ("MAJOR", "전공"), ("SPECIAL", "특화 대상"))


# 1차 필터와 같은 값(나이·소득 상한·코드)으로 만들어, 화면의 자격과 추천에서 거른 기준이 어긋나지 않음
def eligibility_text(detail: PolicyDetailRecord) -> str:
    lines = [f"- 나이: {_age(detail.min_age, detail.max_age)}"]
    income = _income(detail)
    if income:
        lines.append(f"- 소득: {income}")
    if detail.marriage_name in MARRIAGE_NAMES:
        lines.append(f"- 혼인: {detail.marriage_name}")
    for condition_type, label in ELIGIBILITY_LABELS:
        names = [name for name in detail.eligibility_names.get(condition_type, []) if name != NO_RESTRICTION_NAME]
        # "기타"만 있으면 알려 주는 것이 없어 뺌. 다른 값과 섞이면 빼는 순간 대상이 실제보다 좁아 보여 둠
        if names and names != [OTHER_NAME]:
            lines.append(f"- {label}: {', '.join(names)}")
    for title, text in (("추가 자격", detail.additional_qualification), ("참여 제한 대상", detail.participation_exclusion)):
        if clean_text(text):
            lines += [title, clean_text(text)]
    return "\n".join(lines)


# 비어 있으면 사용자가 다음에 할 일을 알 수 있게 확인할 곳을 안내함
# 링크의 절반 이상이 기관 홈페이지 첫 화면이라(B7) 기관 문의를 앞에 두고 링크는 참고로 씀
def missing_text(source_url: str | None, organization: str | None, operator: str | None) -> str:
    organization = _contact(clean_text(organization), clean_text(operator))
    if organization and source_url:
        return f"공고에 나와 있지 않아요. 담당 기관({organization})에 문의하거나 관련 사이트를 참고해 주세요."
    if organization:
        return f"공고에 나와 있지 않아요. 담당 기관({organization})에 문의해 주세요."
    if source_url:
        return "공고에 나와 있지 않아요. 관련 사이트를 참고하거나 정책을 운영하는 기관에 문의해 주세요."
    return "공고에 나와 있지 않아요. 정책을 운영하는 기관에 문의해 주세요."


# 실제 연락할 곳은 운영 기관(공단·재단 등)인 경우가 많지만 "기획예산실"처럼 그것만으로는 어디인지 모르는 이름이 있어
# 다르면 주관 기관과 함께 씀
def _contact(organization: str, operator: str) -> str:
    if organization and operator and operator != organization:
        return f"{organization}, 운영: {operator}"
    return organization or operator


def _age(min_age: int | None, max_age: int | None) -> str:
    low = min_age if min_age is not None and min_age > MIN_AGE_LIMIT else None
    high = max_age if max_age is not None and max_age < MAX_AGE_LIMIT else None
    if low is not None and high is not None:
        return f"만 {low}~{high}세"
    if low is not None:
        return f"만 {low}세 이상"
    if high is not None:
        return f"만 {high}세 이하"
    return "제한 없음"


def _income(detail: PolicyDetailRecord) -> str:
    if detail.income_condition_code == NO_INCOME_LIMIT_CODE:
        return "무관"
    if detail.income_condition_code == ANNUAL_INCOME_CODE:
        # 원 단위로 잘못 들어간 상한은 1차 필터처럼 숫자를 믿지 않음
        high = detail.max_income
        if high is None or high <= 0 or high >= MAX_INCOME_MANWON:
            return "연소득 기준 있음 (공고 확인)"
        low = detail.min_income if detail.min_income and 0 < detail.min_income < high else None
        if low is not None:
            return f"연소득 {low:,}만 원 이상 {high:,}만 원 이하"
        return f"연소득 {high:,}만 원 이하"
    if detail.income_condition_code == OTHER_INCOME_CODE:
        return clean_text(detail.income_etc) or "기준 있음 (공고 확인)"
    return ""


# 원문 앞뒤의 공백·줄바꿈만 있는 칸은 빈 칸으로 봄
def clean_text(text: str | None) -> str:
    return (text or "").strip()
