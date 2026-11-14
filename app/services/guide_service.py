import asyncio
import logging
from datetime import date, datetime
from uuid import UUID

from openai import APITimeoutError, OpenAIError

from app.core.dependencies import PipelineDeps
from app.core.errors import LlmTimeoutError, LlmUpstreamError, PolicyNotFoundError
from app.core.rate_limit import RateLimits
from app.pipeline.conditions import SearchConditions
from app.pipeline.generator import STATUS_LABELS
from app.pipeline.prompts import load_prompt
from app.repositories.chat_repository import ChatRepository
from app.repositories.policy_repository import PolicyDetailRecord
from app.schemas.guide import GuideDraft, GuideResult
from app.schemas.pipeline import ConversationTurn
from app.schemas.policy import POLICY_ID_MAX_CHARS
from app.schemas.policy_info import KST, ApplicationStatus, clean_url, today_kst
from app.services.guide_pdf import GuideContent, render_guide_pdf
from app.services.guide_store import GuideStore
from app.services.policy_detail import clean_text, contact_name, eligibility_lines, missing_text
from app.services.session_profile import get_owned_room, profile_conditions

# 신청 가이드 PDF (계약 4.7). LLM은 공고 원문을 항목으로 나누고 대화에서 사용자 사실을 뽑는 일만 함
# 날짜·금액·링크·기관명·기본 자격 조건은 공고 데이터에서 코드가 넣어 인쇄해 쓰는 문서에 틀린 값이 들어가지 않게 함

logger = logging.getLogger(__name__)

GUIDE_PROMPT_VERSION = "guide_v1"
GUIDE_PATH = "/api/guides/{token}.pdf"
# 사용자 사실은 대부분 질문에 있어 질문은 모두 넣고, 답변은 되묻기("무주택이신가요?")가 보일 만큼만 넣음
GUIDE_MAX_TURNS = 50
GUIDE_ANSWER_CHARS = 300
# 시연 대화에서는 거의 걸리지 않는 안전 상한. 넘으면 오래된 턴부터 뺌
GUIDE_CONVERSATION_MAX_CHARS = 20_000
# LLM이 항목을 너무 잘게 나눠도 문서가 길어지지 않게 함 (프롬프트에도 같은 상한)
GUIDE_LIST_MAX = 12
# 신청 마감이 이 날수 안이면 주의사항 맨 앞에 알림
DEADLINE_NOTICE_DAYS = 7
OUTSIDE_PROFILE_CAUTION = "프로필 조건(지역·나이 등)으로 찾은 추천 정책이 아니에요. '확인할 조건'을 꼭 확인해 주세요."
GUIDE_UPSTREAM_MESSAGE = "가이드를 만드는 중 문제가 생겼어요. 다시 시도해 주세요."
GUIDE_TIMEOUT_MESSAGE = "가이드를 만드는 데 시간이 너무 오래 걸렸어요. 다시 시도해 주세요."

# LLM 입력에 넣는 공고 원문 (이름, PolicyDetailRecord 필드)
_SOURCE_FIELDS = (
    ("지원 내용", "support_content"),
    ("추가 자격", "additional_qualification"),
    ("참여 제한", "participation_exclusion"),
    ("신청 방법", "application_method"),
    ("심사 방법", "screening_method"),
    ("제출 서류", "submission_documents"),
    ("기타 사항", "additional_notes"),
)
_EMPTY_DRAFT = GuideDraft(observed=[], conditions=[], steps=[], documents=[], cautions=[], terms=[])


class GuideService:
    def __init__(self, chat_repository: ChatRepository, pipeline_deps: PipelineDeps, store: GuideStore) -> None:
        self._chat_repository = chat_repository
        self._deps = pipeline_deps
        self._store = store

    async def create(self, room_id: UUID, client_id: str, policy_id: str, limits: RateLimits) -> GuideResult:
        room = await get_owned_room(self._chat_repository, room_id, client_id)
        detail = await self._get_detail(policy_id)
        policy_repository = self._deps.policy_repository
        turns, conditions, labels = await asyncio.gather(
            self._chat_repository.list_recent_turns(room_id, GUIDE_MAX_TURNS),
            profile_conditions(room, policy_repository, self._deps.common_code_repository),
            policy_repository.get_region_labels([policy_id]),
        )

        now = datetime.now(KST)
        # 검색·스크랩으로 연 다른 지역 정책도 막지 않고 경고만 함. 어느 조건이 안 맞는지는 판정하지 않음
        in_profile = await policy_repository.matches_filter(conditions.to_filter(), policy_id, now.date())
        draft = await self._draft(detail, conditions, turns, limits)
        content = guide_content(detail, conditions, draft, labels.get(policy_id), now, in_profile=in_profile)
        # PDF 조립은 CPU 작업이라 이벤트 루프를 막지 않게 따로 돌림
        pdf = await asyncio.to_thread(render_guide_pdf, content)
        saved = await self._store.save(pdf, f"{detail.record.policy_name} 신청 가이드.pdf")
        return GuideResult(
            policy_id=policy_id,
            pdf_url=GUIDE_PATH.format(token=saved.token),
            expires_at=saved.expires_at,
            outside_profile=not in_profile,
        )

    # 상세 API와 같은 기준: 원천에서 사라진 정책은 오래된 정보라 없는 정책으로 봄
    async def _get_detail(self, policy_id: str) -> PolicyDetailRecord:
        if len(policy_id) > POLICY_ID_MAX_CHARS:
            raise PolicyNotFoundError
        detail = await self._deps.policy_repository.get_detail(policy_id)
        if detail is None or not detail.is_active:
            raise PolicyNotFoundError
        return detail

    async def _draft(
        self, detail: PolicyDetailRecord, conditions: SearchConditions, turns: list[ConversationTurn], limits: RateLimits
    ) -> GuideDraft:
        sources = policy_sources(detail)
        # 나눌 원문도 대화도 없으면 LLM이 할 일이 없어 부르지 않음 (하루 상한에도 세지 않음)
        if not sources and not turns:
            return _EMPTY_DRAFT

        limits.use_llm()
        messages = [
            {"role": "system", "content": load_prompt(GUIDE_PROMPT_VERSION)},
            {"role": "user", "content": guide_input(detail.record.policy_name, sources, conditions, turns)},
        ]
        try:
            result = await self._deps.llm.structured(
                messages, GuideDraft, timeout=self._deps.settings.guide_timeout
            )
        except APITimeoutError:
            logger.exception("신청 가이드 시간 초과")
            raise LlmTimeoutError(GUIDE_TIMEOUT_MESSAGE) from None
        except OpenAIError:
            logger.exception("신청 가이드 LLM 호출 실패")
            raise LlmUpstreamError(GUIDE_UPSTREAM_MESSAGE) from None
        # 모델이 응답을 거부하면 파싱 결과가 없음
        if result.parsed is None:
            logger.error("신청 가이드 출력이 비어 있음 (출력 토큰 %d)", result.completion_tokens)
            raise LlmUpstreamError(GUIDE_UPSTREAM_MESSAGE)
        return result.parsed


# (이름, 원문). 빈 항목은 뺌
def policy_sources(detail: PolicyDetailRecord) -> list[tuple[str, str]]:
    return [
        (name, clean_text(getattr(detail, field)))
        for name, field in _SOURCE_FIELDS
        if clean_text(getattr(detail, field))
    ]


def guide_input(
    policy_name: str, sources: list[tuple[str, str]], conditions: SearchConditions, turns: list[ConversationTurn]
) -> str:
    lines = [f"[정책] {policy_name}", "", "[공고 원문]"]
    for name, text in sources:
        lines += [f"## {name}", text]
    if not sources:
        lines.append("없음")
    lines += ["", "[사용자 프로필]", *(conditions.lines or ["정보 없음"]), "", "[대화] (오래된 순)"]
    lines += conversation_lines(turns) or ["없음"]
    return "\n".join(lines)


# 질문은 모두, 답변은 앞부분만. 전체가 상한을 넘으면 오래된 턴부터 뺌
def conversation_lines(turns: list[ConversationTurn]) -> list[str]:
    blocks: list[list[str]] = []
    for turn in turns:
        answer = turn.answer.strip()
        if len(answer) > GUIDE_ANSWER_CHARS:
            answer = answer[:GUIDE_ANSWER_CHARS] + "…(이하 생략)"
        blocks.append([f"질문: {turn.question.strip()}", f"답변 앞부분: {answer}"])

    total = 0
    kept: list[list[str]] = []
    for block in reversed(blocks):
        size = sum(len(line) for line in block)
        if kept and total + size > GUIDE_CONVERSATION_MAX_CHARS:
            break
        kept.append(block)
        total += size
    kept.reverse()
    return [f"{number}. {line}" if index == 0 else f"   {line}"
            for number, block in enumerate(kept, start=1) for index, line in enumerate(block)]


def guide_content(
    detail: PolicyDetailRecord,
    conditions: SearchConditions,
    draft: GuideDraft,
    region_label: str | None,
    now: datetime,
    *,
    in_profile: bool = True,
) -> GuideContent:
    record = detail.record
    item = record.to_item(0.0, now.date(), region_label)
    organization = contact_name(clean_text(record.supervising_org_name), clean_text(detail.operating_org_name))
    application_url = clean_url(record.application_url)
    reference_url = next(
        (url for url in map(clean_url, (record.reference_url_1, record.reference_url_2)) if url and url != application_url),
        None,
    )
    source_url = application_url or reference_url

    overview = [("담당 기관", organization or "정보 없음")]
    if region_label:
        overview.append(("지역", region_label))
    overview.append(("신청 상태", _status_text(item.application_status, record.application_start_date, record.application_end_date)))
    period_raw = clean_text(detail.application_period_raw)
    if period_raw and record.application_start_date is None and record.application_end_date is None:
        overview.append(("신청 기간 (공고)", period_raw))
    if application_url:
        overview.append(("신청 사이트", application_url))
    if reference_url:
        overview.append(("관련 사이트", reference_url))

    return GuideContent(
        policy_name=record.policy_name,
        overview=overview,
        profile=[_split_line(line) for line in conditions.lines],
        observed=[(fact.item, fact.value) for fact in draft.observed[:GUIDE_LIST_MAX] if fact.value.strip()],
        conditions=_conditions(detail, draft),
        steps=[(step.action, step.where) for step in draft.steps[:GUIDE_LIST_MAX] if step.action.strip()],
        documents=_strings(draft.documents),
        documents_raw=clean_text(detail.submission_documents),
        cautions=(
            ([] if in_profile else [OUTSIDE_PROFILE_CAUTION])
            + _code_cautions(detail, item.application_status, now.date())
            + _strings(draft.cautions)
        ),
        terms=[(term.term, term.meaning) for term in draft.terms[:GUIDE_LIST_MAX] if term.meaning.strip()],
        missing=missing_text(source_url, record.supervising_org_name, detail.operating_org_name),
        generated_at=now,
        synced_on=detail.synced_at.astimezone(KST).date() if detail.synced_at else None,
        source_url=source_url,
    )


# 기본 조건(나이·소득·혼인·코드)은 상세 API와 같은 문장, 추가 자격·참여 제한은 LLM이 나눈 항목
# LLM이 나누지 못했으면 원문을 그대로 넣어 조건이 빠지지 않게 함
def _conditions(detail: PolicyDetailRecord, draft: GuideDraft) -> list[str]:
    basic = eligibility_lines(detail)
    extra = _strings(draft.conditions)
    if not extra:
        extra = [
            f"{name}: {clean_text(text)}"
            for name, text in (("추가 자격", detail.additional_qualification), ("참여 제한", detail.participation_exclusion))
            if clean_text(text)
        ]
    return basic + extra


def _code_cautions(detail: PolicyDetailRecord, status: ApplicationStatus, today: date) -> list[str]:
    cautions = []
    end = detail.record.application_end_date
    if status == ApplicationStatus.OPEN and end is not None and (end - today).days <= DEADLINE_NOTICE_DAYS:
        days = (end - today).days
        cautions.append(f"신청 마감이 {end.isoformat()}이에요 ({'오늘 마감' if days == 0 else f'{days}일 남음'}).")
    elif status == ApplicationStatus.CLOSED:
        cautions.append("지금은 신청 기간이 아니에요. 다음 공고 일정을 담당 기관에 확인해 주세요.")
    elif status == ApplicationStatus.UPCOMING and detail.record.application_start_date:
        cautions.append(f"신청은 {detail.record.application_start_date.isoformat()}부터예요.")
    if detail.first_come_first_served:
        cautions.append("선착순으로 마감될 수 있어요.")
    return cautions


def _status_text(status: ApplicationStatus, start: date | None, end: date | None) -> str:
    label = STATUS_LABELS[status]
    if status == ApplicationStatus.ALWAYS_OPEN or (start is None and end is None):
        return label
    return f"{label} ({start.isoformat() if start else '미정'} ~ {end.isoformat() if end else '미정'})"


# 프로필 조건 문장("지역: 서울특별시 강남구")을 표의 (항목, 값)으로
def _split_line(line: str) -> tuple[str, str]:
    label, _, value = line.partition(": ")
    return (label, value) if value else ("", line)


def _strings(values: list[str]) -> list[str]:
    return [value.strip() for value in values[:GUIDE_LIST_MAX] if value.strip()]
