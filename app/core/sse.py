import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

PING = ":ping\n\n"

# 중간 프록시(Vercel, Nginx)가 응답을 모아 보내거나 저장하지 않게 막음 (계약 R4)
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# 계약상 data는 한 줄 JSON이어야 함. json.dumps는 줄바꿈을 \n으로 바꿔 한 줄이 보장됨
def sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


# 이벤트가 오래 안 나와도(질문 분석, 첫 토큰 대기) 간격마다 :ping이 나가게 다음 이벤트를 따로 기다림
async def with_pings(events: AsyncGenerator[str, None], interval: float) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    next_ping = loop.time() + interval
    pending: asyncio.Future[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=max(next_ping - loop.time(), 0))
            if not done:
                yield PING
                next_ping = loop.time() + interval
                continue

            future, pending = pending, None
            try:
                yield future.result()
            except StopAsyncIteration:
                return
    finally:
        # 연결이 끊기면 이벤트 생성도 멈춤 (4c에서는 LLM 호출 취소). 기다리던 중이면 취소, 쉬던 중이면 닫음
        if pending is not None:
            pending.cancel()
        else:
            await events.aclose()
