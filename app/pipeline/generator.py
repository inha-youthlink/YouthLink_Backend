import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.pipeline.cards import status_of
from app.pipeline.context import PipelineContext
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.policy_info import ApplicationStatus, clean_url

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

NO_RESTRICTION_NAME = "제한없음"
MISSING = "정보 없음"
NO_EVIDENCE = "검색된 정책 없음"

# 첫 줄은 카드로 보여 줄 근거 번호. 스트리밍 때도 첫 줄만 모아 카드를 먼저 보낼 수 있는 형식
# "[소개: 없음]", "[소개: 1번, 3번]"처럼 형식을 조금 벗어나도 숫자만 읽어 후보 전체 표시로 넘어가지 않게 함
CARD_LINE = re.compile(r"\[소개\s*:([^\]]*)\]")

PROFILE_CODE_LABELS = {
    "marriage_status_code": "혼인 상태",
    "major_code": "전공",
    "job_code": "취업 상태",
    "school_code": "학력",
    "special_target_code": "특화 대상",
}


# 근거는 LLM이 읽는 문장이라 응답 코드 대신 한국어로 씀
STATUS_LABELS = {
    ApplicationStatus.OPEN: "신청 가능",
    ApplicationStatus.ALWAYS_OPEN: "상시 모집",
    ApplicationStatus.UPCOMING: "신청 예정",
    ApplicationStatus.CLOSED: "마감",
    ApplicationStatus.UNKNOWN: "정보 없음",
}


@dataclass(frozen=True)
class GenerationResult:
    answer: str
    prompt_version: str
    # 평가 서버가 근거 템플릿을 복제하지 않도록 LLM에 넘긴 정책별 근거 블록을 그대로 전달함
    contexts: list[str]
    # 카드로 보여 줄 근거 번호 (1부터, 오름차순). None이면 첫 줄 형식을 어긴 경우
    card_numbers: list[int] | None
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class CardLine:
    numbers: list[int] | None
    body: str


async def generate(ctx: PipelineContext, prompt_name: str = "generate_v3") -> GenerationResult:
    system_prompt = (PROMPTS_DIR / f"{prompt_name}.md").read_text(encoding="utf-8")
    profile_text = await _format_profile(ctx)
    contexts = _format_evidence(ctx.candidates, ctx.policies, ctx.documents, ctx.today)
    evidence = "\n\n".join(contexts) if contexts else NO_EVIDENCE
    user_message = "\n\n".join([
        f"[오늘 날짜]\n{ctx.today.isoformat()}",
        f"[사용자 프로필]\n{profile_text}",
        f"[근거]\n{evidence}",
        f"[질문]\n{ctx.query}",
    ])

    result = await ctx.deps.llm.chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ])
    card_line = parse_card_line(result.text, len(contexts))
    return GenerationResult(
        answer=card_line.body,
        prompt_version=prompt_name,
        contexts=contexts,
        card_numbers=card_line.numbers,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )


def parse_card_line(text: str, candidate_count: int) -> CardLine:
    first, _, rest = text.strip().partition("\n")
    match = CARD_LINE.fullmatch(first.strip())
    if match is None:
        return CardLine(numbers=None, body=text.strip())
    # LLM은 빼기만 하고 순서는 코드가 정한 후보 순서를 따르도록 정렬함. 없는 번호는 버림
    numbers = sorted({int(n) for n in re.findall(r"\d+", match.group(1)) if 1 <= int(n) <= candidate_count})
    return CardLine(numbers=numbers, body=rest.strip())


async def _format_profile(ctx: PipelineContext) -> str:
    profile = ctx.input.profile
    codes = [getattr(profile, field) for field in PROFILE_CODE_LABELS if getattr(profile, field)]
    code_names = await ctx.deps.common_code_repository.get_names(codes)

    lines = []
    if profile.age is not None:
        lines.append(f"나이: {profile.age}세")
    if profile.gender:
        lines.append(f"성별: {profile.gender}")
    if profile.annual_income is not None:
        lines.append(f"연소득: {_format_won(profile.annual_income)}")
    for field, label in PROFILE_CODE_LABELS.items():
        name = code_names.get(getattr(profile, field) or "")
        if name:
            # 정책 조건의 "제한없음"과 구분되도록 사용자 입장의 표현으로 바꿈
            lines.append(f"{label}: {'해당 없음' if name == NO_RESTRICTION_NAME else name}")
    return "\n".join(lines) if lines else MISSING


# 근거 번호가 카드 번호와 맞아야 해서 후보마다 정확히 한 블록을 만듦
def _format_evidence(
    candidates: list[PolicyGroup],
    policies: dict[str, PolicyRecord],
    documents: dict[str, list[str]],
    today: date,
) -> list[str]:
    blocks = []
    for number, group in enumerate(candidates, start=1):
        policy = policies[group.policy_no]
        application_url = clean_url(policy.application_url)
        # 정책 번호는 답변에 노출되지 않도록 근거에 넣지 않음
        blocks.append("\n".join([
            f"<정책 {number}> {policy.policy_name}",
            f"[신청 상태] {_format_status(policy, today)}",
            f"[신청 링크] {application_url or MISSING}",
            f"[참고 링크] {_reference_url(policy, application_url) or MISSING}",
            f"[담당 기관] {policy.supervising_org_name or MISSING}",
            "[정책 정보]",
            *(documents.get(group.policy_no) or [MISSING]),
        ]))
    return blocks


def _reference_url(policy: PolicyRecord, application_url: str | None) -> str | None:
    # 신청 링크와 같은 주소가 많아(278건) 같은 링크를 두 번 안내하지 않도록 뺌
    for value in (policy.reference_url_1, policy.reference_url_2):
        url = clean_url(value)
        if url and url != application_url:
            return url
    return None


def _format_status(policy: PolicyRecord, today: date) -> str:
    status = status_of(policy, today)
    label = STATUS_LABELS[status]
    start, end = policy.application_start_date, policy.application_end_date
    # 마감 코드 정책은 날짜가 없어 "미정 ~ 미정"이 붙지 않도록 상태만 표시함
    if status == ApplicationStatus.ALWAYS_OPEN or (start is None and end is None):
        return label
    start = start.isoformat() if start else "미정"
    end = end.isoformat() if end else "미정"
    return f"{label} (신청 기간: {start} ~ {end})"


def _format_won(amount: int) -> str:
    if amount >= 10_000 and amount % 10_000 == 0:
        return f"{amount // 10_000:,}만 원"
    return f"{amount:,}원"
