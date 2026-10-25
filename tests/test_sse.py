import asyncio
import json

from app.core.sse import PING, sse_event, with_pings


def test_event_data_is_one_line_json():
    event = sse_event("token", "첫 줄\n둘째 줄")

    assert event == 'event: token\ndata: "첫 줄\\n둘째 줄"\n\n'
    assert json.loads(event.split("data: ", 1)[1]) == "첫 줄\n둘째 줄"


async def _collect(stream) -> list[str]:
    return [item async for item in stream]


async def test_ping_while_waiting_for_event():
    async def slow():
        await asyncio.sleep(0.25)
        yield "A"

    items = await _collect(with_pings(slow(), interval=0.1))

    assert items == [PING, PING, "A"]


async def test_ping_keeps_interval_between_fast_events():
    async def steady():
        for index in range(6):
            await asyncio.sleep(0.04)
            yield str(index)

    items = await _collect(with_pings(steady(), interval=0.1))

    # 이벤트가 계속 나와도 간격마다 :ping이 끼어듦 (계약 §5.4 "15초마다 :ping")
    assert [item for item in items if item != PING] == ["0", "1", "2", "3", "4", "5"]
    # 0.24초 동안 0.1초 간격이라 2번. 느린 환경의 지연을 감안해 3번까지 허용
    assert items.count(PING) in (2, 3)


async def test_no_ping_when_events_are_quick():
    async def quick():
        yield "A"
        yield "B"

    assert await _collect(with_pings(quick(), interval=1)) == ["A", "B"]


async def test_cancel_stops_waiting_source():
    finished = asyncio.Event()

    async def endless():
        try:
            await asyncio.sleep(10)
            yield "never"
        finally:
            finished.set()

    task = asyncio.create_task(_collect(with_pings(endless(), interval=1)))
    await asyncio.sleep(0.05)
    task.cancel()

    # 연결이 끊겨 취소되면 기다리던 이벤트 생성도 멈춰야 함 (4c에서는 LLM 호출)
    await asyncio.wait_for(finished.wait(), timeout=1)


async def test_close_stops_idle_source():
    closed = asyncio.Event()

    async def source():
        try:
            yield "A"
            yield "B"
        finally:
            closed.set()

    stream = with_pings(source(), interval=1)
    assert await anext(stream) == "A"
    await stream.aclose()

    assert closed.is_set()
