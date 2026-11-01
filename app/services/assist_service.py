from collections import Counter
from dataclasses import replace
from uuid import UUID

from app.core.dependencies import PipelineDeps
from app.core.errors import SessionNotFoundError
from app.pipeline.conditions import RegionIndex, build_conditions, profile_codes
from app.repositories.chat_repository import ChatRepository
from app.schemas.analysis import Category
from app.schemas.policy_info import CATEGORY_NAMES, today_kst
from app.schemas.session import PolicySummary

# 대화 보조 (계약 4.2): 채팅방의 "추천 정책"·"대화 요약" 버튼
MAX_RECOMMENDATIONS = 10
_CATEGORY_VALUES = frozenset(category.value for category in Category)


class AssistService:
    def __init__(self, chat_repository: ChatRepository, pipeline_deps: PipelineDeps) -> None:
        self._chat_repository = chat_repository
        self._deps = pipeline_deps

    # 프로필로 1차 필터를 건 뒤 대화에서 나온 분야로 좁힘 (계약 B3). 분야가 없으면 프로필 기준만
    # 대화 문장이 아니라 저장된 질문 분석의 분야를 읽어 LLM을 부르지 않음
    async def recommend(self, room_id: UUID, client_id: str) -> list[PolicySummary]:
        room = await self._chat_repository.get_room(room_id)
        if room is None or room.user_id != client_id:
            raise SessionNotFoundError

        policy_repository = self._deps.policy_repository
        regions = RegionIndex(await policy_repository.list_regions())
        code_names = await self._deps.common_code_repository.get_names(profile_codes(room.profile))
        conditions = build_conditions(None, room.profile, regions, code_names)
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
