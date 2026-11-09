from uuid import UUID

from app.core.errors import SessionNotFoundError
from app.pipeline.conditions import RegionIndex, SearchConditions, build_conditions, profile_codes
from app.repositories.chat_repository import ChatRepository, RoomRecord
from app.repositories.common_code_repository import CommonCodeRepository
from app.repositories.policy_repository import PolicyRepository

# 세션 프로필로 정책을 거르는 공개 API(추천·요약, 세션 정책 검색)가 함께 씀


# 다른 사용자의 세션이 있는지 숨기려고 없는 경우와 같은 응답을 씀
async def get_owned_room(chat_repository: ChatRepository, room_id: UUID, client_id: str) -> RoomRecord:
    room = await chat_repository.get_room(room_id)
    if room is None or room.user_id != client_id:
        raise SessionNotFoundError
    return room


# 채팅 검색과 같은 프로필 조건 (1차 필터, 표시 문구). 질문이 없어 프로필 값만으로 만듦
async def profile_conditions(
    room: RoomRecord, policy_repository: PolicyRepository, common_code_repository: CommonCodeRepository
) -> SearchConditions:
    regions = RegionIndex(await policy_repository.list_regions())
    code_names = await common_code_repository.get_names(profile_codes(room.profile))
    return build_conditions(None, room.profile, regions, code_names)
