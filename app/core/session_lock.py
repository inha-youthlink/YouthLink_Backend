import time
from collections.abc import Callable

# 정상 답변은 1분 안에 끝남. 연결이 스트림 시작 전에 끊겨 해제 코드가 돌지 못해도 이 시간이 지나면 다시 받음
LOCK_TTL_SECONDS = 300.0


# 세션마다 답변 생성을 하나만 둠. 요청 제한처럼 프로세스 메모리에 있어 워커는 하나여야 함
class SessionLocks:
    def __init__(self, ttl: float = LOCK_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._held: dict[str, float] = {}

    def acquire(self, key: str) -> bool:
        now = self._clock()
        started = self._held.get(key)
        if started is not None and now - started < self._ttl:
            return False
        self._held[key] = now
        return True

    def release(self, key: str) -> None:
        self._held.pop(key, None)
