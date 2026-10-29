import asyncio
from collections.abc import AsyncGenerator
from uuid import UUID

from app.core.config import Settings
from app.core.sse import sse_event
from app.schemas.policy_info import ApplicationStatus
from app.schemas.session import NATIONWIDE, PolicyCategory, PolicySummary

# 프론트가 카드를 그려 볼 수 있게 계약 형식 그대로 둔 검증용 정책
SKELETON_POLICY = PolicySummary(
    id="SKELETON-1",
    title="SSE 검증용 정책",
    category=PolicyCategory.WELFARE,
    apply_start=None,
    apply_end=None,
    status=ApplicationStatus.ALWAYS_OPEN,
    region_label="전국",
    region=NATIONWIDE,
    organization="YouthLink",
    source_url=None,
)
# token 안의 줄바꿈이 JSON 문자열로 잘 넘어가는지도 보려고 주기적으로 넣음
NEWLINE_EVERY = 10


# RAG 없이 계약 §5.4 조건(첫 token 전 대기, 일정 간격 token, policies, done)대로 흘려보내는 채팅 뼈대
async def skeleton_events(client_message_id: UUID, settings: Settings) -> AsyncGenerator[str, None]:
    await asyncio.sleep(settings.chat_skeleton_first_delay)

    total = settings.chat_skeleton_tokens
    for index in range(1, total + 1):
        newline = "\n" if index % NEWLINE_EVERY == 0 else ""
        yield sse_event("token", f"검증용 답변 {index}/{total}. {newline}")
        if index < total:
            await asyncio.sleep(settings.chat_skeleton_token_interval)

    yield sse_event("policies", [SKELETON_POLICY.model_dump(by_alias=True, mode="json")])
    # 같은 clientMessageId로 다시 보내면 같은 id가 나가 프론트의 중복 방지(id 기준)가 실제 재전송과 같게 동작함
    yield sse_event("done", {"messageId": f"skeleton-{client_message_id}"})
