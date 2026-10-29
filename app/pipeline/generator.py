import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

from app.llm.client import ChatUsage
from app.pipeline.cards import status_of
from app.pipeline.conditions import load_code_names, load_regions
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import AnswerKind
from app.pipeline.grouping import PolicyGroup
from app.pipeline.prompts import load_prompt
from app.repositories.policy_repository import PolicyRecord
from app.schemas.policy_info import ApplicationStatus, clean_url

COMMON_PROMPT = "answer_common_v2"
# 카드를 LLM이 고를 때만 첫 줄 [소개: …] 형식을 붙임
CARDS_PROMPT = "answer_cards_v2"
KIND_PROMPTS = {
    AnswerKind.RECOMMEND: "answer_recommend_v1",
    AnswerKind.DETAIL: "answer_detail_v1",
}
# 근거가 없어 근거 사용 규칙이 담긴 공통 조각을 넣지 않음
SHORT_PROMPT = "answer_short_v1"

NO_RESTRICTION_NAME = "제한없음"
MISSING = "정보 없음"
NO_EVIDENCE = "검색된 정책 없음"

# 첫 줄은 카드로 보여 줄 근거 번호. 스트리밍 때도 첫 줄만 모아 카드를 먼저 보낼 수 있는 형식
# "[소개: 없음]", "[소개: 1번, 3번]"처럼 형식을 조금 벗어나도 숫자만 읽어 후보 전체 표시로 넘어가지 않게 함
CARD_LINE = re.compile(r"\[소개\s*:([^\]]*)\]")
# "[소개: 1번, 3번]"도 20자 안팎이라, 이만큼 줄바꿈이 없으면 첫 줄 형식이 아니라고 보고 본문을 바로 흘려보냄
CARD_LINE_MAX_CHARS = 40

PROFILE_CODE_LABELS = {
    "marriage_status_code": "혼인 상태",
    "major_code": "전공",
    "job_code": "취업 상태",
    "school_code": "학력",
    "special_target_code": "특화 대상",
}


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
    # 조립한 조각 이름을 +로 이음. LLM을 부르지 않은 답변은 None
    prompt_version: str | None
    # 평가 서버가 근거 템플릿을 복제하지 않도록 LLM에 넘긴 정책별 근거 블록을 그대로 전달함
    contexts: list[str]
    # 카드로 보여 줄 근거 번호 (1부터, 오름차순). None이면 첫 줄 형식을 어긴 경우
    card_numbers: list[int] | None
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class CardLine:
    numbers: list[int] | None
    body: str


# 스트림 이벤트: 카드 번호(한 번) → 본문 조각들 → GenerationResult. 카드 번호는 항상 첫 본문 조각보다 먼저 나감
@dataclass(frozen=True)
class CardsDecided:
    # None이면 첫 줄 형식을 어긴 경우
    numbers: list[int] | None


@dataclass(frozen=True)
class AnswerDelta:
    text: str


type GenerationEvent = CardsDecided | AnswerDelta | GenerationResult


async def generate_stream(ctx: PipelineContext) -> AsyncIterator[GenerationEvent]:
    evidence = ctx.evidence
    all_cards = list(range(1, len(evidence.candidates) + 1))
    if evidence.reply is not None:
        yield CardsDecided(all_cards)
        yield AnswerDelta(evidence.reply)
        yield GenerationResult(answer=evidence.reply, prompt_version=None, contexts=[], card_numbers=all_cards)
        return

    if ctx.answer_kind == AnswerKind.SHORT:
        prompt_names = [SHORT_PROMPT]
        contexts: list[str] = []
        user_message = f"[질문]\n{ctx.query}"
    else:
        prompt_names = [
            COMMON_PROMPT, *([] if evidence.fixed_cards else [CARDS_PROMPT]), KIND_PROMPTS[ctx.answer_kind]
        ]
        contexts = _format_evidence(evidence.candidates, evidence.policies, evidence.documents, ctx.today)
        user_message = await _user_message(ctx, contexts)

    system_prompt = "\n\n".join(load_prompt(name) for name in prompt_names)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    # 카드가 미리 정해지면 첫 줄을 기다리지 않고 바로 카드를 보냄
    waiting_card_line = not evidence.fixed_cards
    if not waiting_card_line:
        yield CardsDecided(all_cards)
    head = ""
    numbers: list[int] | None = all_cards
    body = _Body()
    usage = ChatUsage(prompt_tokens=0, completion_tokens=0)

    async for piece in ctx.deps.llm.chat_stream(messages):
        if isinstance(piece, ChatUsage):
            usage = piece
            continue
        if waiting_card_line:
            head += piece
            decided = _decide_card_line(head, len(contexts))
            if decided is None:
                continue
            waiting_card_line, numbers = False, decided.numbers
            yield CardsDecided(numbers)
            piece = decided.body
        text = body.feed(piece)
        if text:
            yield AnswerDelta(text)

    # 첫 줄 판단 전에 끝난 짧은 답변("[소개: 1]"만 있거나 줄바꿈 없는 한 줄)은 한 번에 받을 때와 같이 파싱함
    if waiting_card_line:
        card_line = parse_card_line(head, len(contexts))
        numbers = card_line.numbers
        yield CardsDecided(numbers)
        text = body.feed(card_line.body)
        if text:
            yield AnswerDelta(text)

    yield GenerationResult(
        answer=body.text,
        prompt_version="+".join(prompt_names),
        contexts=contexts,
        card_numbers=numbers,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
    )


# 스트림으로 내보낸 본문을 모으면 한 번에 받을 때의 strip() 결과와 같도록 앞 공백은 버리고 뒤 공백은 다음 글자가 올 때까지 보류함
class _Body:
    def __init__(self) -> None:
        self.text = ""
        self._pending = ""

    def feed(self, piece: str) -> str:
        if not self.text:
            piece = piece.lstrip()
        stripped = piece.rstrip()
        if not stripped:
            if self.text:
                self._pending += piece
            return ""
        out = self._pending + stripped
        self._pending = piece[len(stripped):]
        self.text += out
        return out


# 첫 줄을 다 받았거나 첫 줄 형식이 아님이 분명해지면 판단함. 아직 모르면 None
# 본문은 아직 다 받지 않아 strip하지 않고 넘김 (조각 경계의 공백을 지우지 않도록 공백 처리는 _Body가 함)
def _decide_card_line(head: str, candidate_count: int) -> CardLine | None:
    text = head.lstrip()
    if not text:
        return None
    if "\n" in text:
        first, _, rest = text.partition("\n")
        match = CARD_LINE.fullmatch(first.strip())
        if match is None:
            return CardLine(numbers=None, body=text)
        return CardLine(numbers=_card_numbers(match, candidate_count), body=rest)
    # 줄 끝 공백은 한 번에 받을 때 strip()으로 지워지므로 길이에 넣지 않음
    if not text.startswith("[") or len(text.rstrip()) > CARD_LINE_MAX_CHARS:
        return CardLine(numbers=None, body=text)
    return None


async def _user_message(ctx: PipelineContext, contexts: list[str]) -> str:
    sections = [
        f"[오늘 날짜]\n{ctx.today.isoformat()}",
        f"[사용자 프로필]\n{await _format_profile(ctx)}",
    ]
    # 근거가 조건으로 걸러졌음을 알아야 프로필과 다른 조건을 안내하고, 결과가 없을 때 이유를 말할 수 있음
    if ctx.evidence.filtered and ctx.conditions and ctx.conditions.lines:
        sections.append("[검색 조건]\n" + "\n".join(ctx.conditions.lines))
    evidence = "\n\n".join(contexts) if contexts else NO_EVIDENCE
    sections += [f"[근거]\n{evidence}", f"[질문]\n{ctx.query}"]
    return "\n\n".join(sections)


def parse_card_line(text: str, candidate_count: int) -> CardLine:
    first, _, rest = text.strip().partition("\n")
    match = CARD_LINE.fullmatch(first.strip())
    if match is None:
        return CardLine(numbers=None, body=text.strip())
    return CardLine(numbers=_card_numbers(match, candidate_count), body=rest.strip())


# LLM은 빼기만 하고 순서는 코드가 정한 후보 순서를 따르도록 정렬함. 없는 번호는 버림
def _card_numbers(match: re.Match[str], candidate_count: int) -> list[int]:
    return sorted({int(n) for n in re.findall(r"\d+", match.group(1)) if 1 <= int(n) <= candidate_count})


async def _format_profile(ctx: PipelineContext) -> str:
    profile = ctx.input.profile
    code_names = await load_code_names(ctx)

    lines = []
    region = (await load_regions(ctx)).profile_region(profile.region_code) if profile.region_code else None
    if region:
        lines.append(f"지역: {region.label}")
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
    # 신청 링크와 같은 주소면 두 번 안내하지 않도록 뺌
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
