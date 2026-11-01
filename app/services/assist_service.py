import logging
from collections import Counter
from dataclasses import replace
from uuid import UUID

from openai import APITimeoutError, OpenAIError

from app.core.dependencies import PipelineDeps
from app.core.errors import LlmTimeoutError, LlmUpstreamError, SessionNotFoundError
from app.core.rate_limit import RateLimits
from app.pipeline.conditions import (
    RegionIndex,
    SearchConditions,
    build_conditions,
    profile_codes,
)
from app.pipeline.prompts import load_prompt
from app.repositories.chat_repository import ChatRepository, RoomRecord
from app.schemas.analysis import Category
from app.schemas.pipeline import ConversationTurn
from app.schemas.policy_info import CATEGORY_NAMES, today_kst
from app.schemas.session import PolicySummary

# 대화 보조 (계약 4.2): 채팅방의 "추천 정책"·"대화 요약" 버튼
MAX_RECOMMENDATIONS = 10
_CATEGORY_VALUES = frozenset(category.value for category in Category)

logger = logging.getLogger(__name__)

SUMMARY_PROMPT_VERSION = "summary_v1"
SUMMARY_TURNS = 10
# 지금 답변(250~460자)은 끝부분 결론까지 다 들어가고, 정책을 많이 소개하는 긴 답변만 잘림 (11-01 비교: 300자는 결론이 빠짐)
SUMMARY_ANSWER_CHARS = 1000
# 한 번에 받는 응답이라 다 쓸 때까지 화면이 비어 있음. 출력 상한은 추론 토큰까지 포함한 안전장치
SUMMARY_MAX_OUTPUT_TOKENS = 1000
# 기본값보다 출력 토큰이 절반이고 짧은 요약에 더 가까움. "minimal"은 게이트웨이가 받지 않음 (11-01 확인)
SUMMARY_REASONING_EFFORT = "low"
EMPTY_SUMMARY = "아직 나눈 대화가 없습니다."
SUMMARY_UPSTREAM_MESSAGE = "요약을 만드는 중 문제가 생겼어요. 다시 시도해 주세요."
SUMMARY_TIMEOUT_MESSAGE = "요약을 만드는 데 시간이 너무 오래 걸렸어요. 다시 시도해 주세요."


class AssistService:
    def __init__(self, chat_repository: ChatRepository, pipeline_deps: PipelineDeps) -> None:
        self._chat_repository = chat_repository
        self._deps = pipeline_deps

    async def summarize(self, room_id: UUID, client_id: str, limits: RateLimits) -> str:
        room = await self._get_owned_room(room_id, client_id)
        turns = await self._chat_repository.list_recent_turns(room_id, SUMMARY_TURNS)
        # 대화가 없으면 LLM을 부르지 않아 하루 상한에도 세지 않음
        if not turns:
            return EMPTY_SUMMARY

        conditions = await self._profile_conditions(room)
        limits.use_llm()
        messages = [
            {"role": "system", "content": load_prompt(SUMMARY_PROMPT_VERSION)},
            {"role": "user", "content": summary_input(conditions, turns)},
        ]
        try:
            # 재시도하면 시간 제한(30초)을 몇 번 기다릴 수 있어 한 번만 부름
            result = await self._deps.llm.chat(
                messages,
                max_output_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
                reasoning_effort=SUMMARY_REASONING_EFFORT,
                max_retries=0,
            )
        except APITimeoutError:
            logger.exception("대화 요약 시간 초과")
            raise LlmTimeoutError(SUMMARY_TIMEOUT_MESSAGE) from None
        except OpenAIError:
            logger.exception("대화 요약 LLM 호출 실패")
            raise LlmUpstreamError(SUMMARY_UPSTREAM_MESSAGE) from None
        # 출력 상한에는 추론 토큰도 포함돼, 추론에 다 쓰면 본문 없이 끝날 수 있음. 대화가 있으므로 생성 실패로 봄
        summary = result.text.strip()
        if not summary:
            logger.error("대화 요약 출력이 비어 있음 (출력 토큰 %d)", result.completion_tokens)
            raise LlmUpstreamError(SUMMARY_UPSTREAM_MESSAGE)
        return summary

    # 프로필로 1차 필터를 건 뒤 대화에서 나온 분야로 좁힘 (계약 B3). 분야가 없으면 프로필 기준만
    # 대화 문장이 아니라 저장된 질문 분석의 분야를 읽어 LLM을 부르지 않음
    async def recommend(self, room_id: UUID, client_id: str) -> list[PolicySummary]:
        room = await self._get_owned_room(room_id, client_id)
        policy_repository = self._deps.policy_repository
        conditions = await self._profile_conditions(room)
        # 채팅이 대화 기록으로 쓰는 턴 수만큼 봐 주제가 바뀌면 추천도 따라감
        category = conversation_category(
            await self._chat_repository.list_recent_categories(room_id, self._deps.settings.history_turns)
        )
        # 마감은 신청할 수 없어 빼고, 신청 가능한 정책 먼저 (진행 중 → 상시 → 예정 → 정보 없음, 계약 4.2)
        policy_filter = replace(
            conditions.to_filter(),
            exclude_closed=True,
            large_categories=tuple(sorted(CATEGORY_NAMES[category])) if category else (),
        )

        today = today_kst()
        records, _ = await policy_repository.search(
            policy_filter, today, 0, MAX_RECOMMENDATIONS, available_first=True
        )
        labels = await policy_repository.get_region_labels([record.policy_no for record in records])
        return [
            PolicySummary.from_item(record.to_item(0.0, today, labels.get(record.policy_no)))
            for record in records
        ]


    # 다른 사용자의 세션이 있는지 숨기려고 없는 경우와 같은 응답을 씀
    async def _get_owned_room(self, room_id: UUID, client_id: str) -> RoomRecord:
        room = await self._chat_repository.get_room(room_id)
        if room is None or room.user_id != client_id:
            raise SessionNotFoundError
        return room

    # 채팅 검색과 같은 프로필 조건 (1차 필터, 표시 문구)
    async def _profile_conditions(self, room: RoomRecord) -> SearchConditions:
        regions = RegionIndex(await self._deps.policy_repository.list_regions())
        code_names = await self._deps.common_code_repository.get_names(profile_codes(room.profile))
        return build_conditions(None, room.profile, regions, code_names)


# 오래된 턴부터. 답변은 앞부분만, 카드는 정책 이름만
def summary_input(conditions: SearchConditions, turns: list[ConversationTurn]) -> str:
    lines = ["[사용자 조건]", *(conditions.lines or ["정보 없음"]), "", "[대화] (오래된 순)"]
    for number, turn in enumerate(turns, start=1):
        answer = turn.answer.strip()
        if len(answer) > SUMMARY_ANSWER_CHARS:
            answer = answer[:SUMMARY_ANSWER_CHARS] + "…(이하 생략)"
        lines += [f"{number}. 질문: {turn.question.strip()}", f"   답변 앞부분: {answer}"]
        if turn.policies:
            lines.append(f"   안내한 정책: {', '.join(policy.policy_name for policy in turn.policies)}")
    return "\n".join(lines)


# 최근 답변(최근부터)의 분야 중 가장 많이 나온 것. 동률이면 더 최근 것, "없음"·모르는 값은 세지 않음
def conversation_category(recent: list[list[str]]) -> Category | None:
    ordered = [
        Category(name) for names in recent for name in names
        if name in _CATEGORY_VALUES and name != Category.NONE
    ]
    if not ordered:
        return None
    counts = Counter(ordered)
    best = max(counts.values())
    return next(category for category in ordered if counts[category] == best)
