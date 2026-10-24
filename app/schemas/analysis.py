from enum import StrEnum

from pydantic import BaseModel


class Intent(StrEnum):
    POLICY_LIST = "policy_list"
    POLICY_DETAIL = "policy_detail"
    ELIGIBILITY = "eligibility"
    COMPARISON = "comparison"
    CHITCHAT = "chitchat"
    OUT_OF_SCOPE = "out_of_scope"
    UNCLEAR = "unclear"


class StatusFilter(StrEnum):
    NONE = "none"
    OPEN_ONLY = "open_only"
    INCLUDE_CLOSED = "include_closed"


# 값을 공통코드 이름과 같게 두어 코드 변환표가 이름만으로 읽힘
class JobStatus(StrEnum):
    NONE = "없음"
    EMPLOYED = "재직자"
    SELF_EMPLOYED = "자영업자"
    UNEMPLOYED = "미취업자"
    FREELANCER = "프리랜서"
    DAILY_WORKER = "일용근로자"
    STARTUP = "(예비)창업자"
    SHORT_TERM_WORKER = "단기근로자"
    FARMER = "영농종사자"


# 정책 대분류(large_categories)의 옛 분류와 새 분류를 하나로 묶은 이름
class Category(StrEnum):
    NONE = "없음"
    JOB = "일자리"
    HOUSING = "주거"
    EDUCATION = "교육"
    WELFARE_CULTURE = "복지문화"
    PARTICIPATION = "참여"


class SchoolLevel(StrEnum):
    NONE = "없음"
    BELOW_HIGH_SCHOOL = "고졸 미만"
    HIGH_SCHOOL_ENROLLED = "고교 재학"
    HIGH_SCHOOL_EXPECTED = "고졸 예정"
    HIGH_SCHOOL_GRADUATED = "고교 졸업"
    COLLEGE_ENROLLED = "대학 재학"
    COLLEGE_EXPECTED = "대졸 예정"
    COLLEGE_GRADUATED = "대학 졸업"
    GRADUATE_SCHOOL = "석·박사"


# 구조화 출력은 모든 필드를 필수로 요구해 기본값을 두지 않고, 개수 제한은 출력 검증에서 확인함
class SubQuery(BaseModel):
    # LLM은 앞 필드부터 생성하므로 맥락 해소(독립 질문, 대상)를 분류보다 앞에 둠
    standalone_question: str
    # 조건은 필터가 거르므로 검색어에 남기면 지명 같은 단어가 주제보다 유사도를 크게 흔듦
    search_query: str
    # 긴 policy_no를 잘못 옮겨 쓰는 오류를 피하려고 프롬프트 후보 표기("2", "C1")를 받음
    target_refs: list[str]
    intent: Intent
    status_filter: StatusFilter
    # 질문에서 사용자가 직접 말한 조건. 말하지 않았으면 "", null, "없음"
    region: str
    age: int | None
    job_status: JobStatus
    school: SchoolLevel
    category: Category


class QueryAnalysis(BaseModel):
    # 단일 → 목록 전환 시 프롬프트, 검증, trace를 모두 고쳐야 해서 처음부터 목록으로 둠
    sub_queries: list[SubQuery]
    is_new_topic: bool
