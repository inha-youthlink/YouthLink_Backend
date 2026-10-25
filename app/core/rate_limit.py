import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import Request, status

from app.core.config import Settings
from app.core.errors import ApiError
from app.schemas.policy_info import KST

# 오래된 키를 지우는 기준. 키가 이만큼 쌓이면 지난 구간의 키를 한 번에 정리함
_SWEEP_SIZE = 10_000


class RateLimitedError(ApiError):
    def __init__(self, retry_after: float, message: str = "요청이 많아요. 잠시 후 다시 시도해 주세요.") -> None:
        super().__init__(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "RATE_LIMITED",
            message,
            headers={"Retry-After": str(max(math.ceil(retry_after), 1))},
        )


# 고정 구간(예: 1분) 단위로 셈. 구간 경계에서 순간적으로 2배까지 허용되지만 남용을 막는 데는 충분함
# 횟수는 프로세스 메모리에만 있어 재시작하면 0이 되고, 워커가 여러 개면 따로 셈
@dataclass
class WindowLimiter:
    limit: int
    window: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _counts: dict[str, tuple[float, int]] = field(default_factory=dict)

    # 넘었으면 다시 시도할 수 있을 때까지 남은 초, 아니면 None
    def hit(self, key: str) -> float | None:
        if self.limit <= 0:
            return None
        now = self.clock()
        start, count = self._counts.get(key, (now, 0))
        if now - start >= self.window:
            start, count = now, 0
        if count >= self.limit:
            return start + self.window - now
        self._counts[key] = (start, count + 1)
        if len(self._counts) > _SWEEP_SIZE:
            self._sweep(now)
        return None

    def _sweep(self, now: float) -> None:
        self._counts = {
            key: value for key, value in self._counts.items() if now - value[0] < self.window
        }


# 하루 단위는 한국 날짜 기준으로 자정에 초기화함
@dataclass
class DailyLimiter:
    limit: int
    now: Callable[[], datetime] = lambda: datetime.now(KST)
    _day: str = ""
    _count: int = 0

    def hit(self) -> float | None:
        if self.limit <= 0:
            return None
        now = self.now()
        today = now.date().isoformat()
        if today != self._day:
            self._day, self._count = today, 0
        if self._count >= self.limit:
            midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=KST)
            return (midnight - now).total_seconds()
        self._count += 1
        return None


class LlmDailyLimitError(RateLimitedError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(retry_after, "오늘 이용량이 많아 채팅을 잠시 멈췄어요. 내일 다시 이용해 주세요.")


class RateLimits:
    def __init__(self, settings: Settings) -> None:
        self.trust_proxy_headers = settings.trust_proxy_headers
        self.api_per_ip = WindowLimiter(settings.rate_limit_per_minute)
        self.chat_per_ip = WindowLimiter(settings.chat_rate_limit_per_minute)
        self.chat_per_client = WindowLimiter(settings.chat_client_rate_limit_per_minute)
        self.llm_daily = DailyLimiter(settings.llm_daily_limit)

    # 실제로 LLM을 부르기 직전에 셈. 없는 세션처럼 앞에서 거절된 요청은 세지 않음
    def use_llm(self) -> None:
        retry_after = self.llm_daily.hit()
        if retry_after is not None:
            raise LlmDailyLimitError(retry_after)

    # Vercel(과 Nginx)을 거치면 실제 접속 IP는 X-Forwarded-For 첫 값에 있음
    # 직접 접속하면 이 헤더를 꾸밀 수 있어, 믿지 않는 설정이면 연결한 쪽 IP를 씀
    def client_ip(self, request: Request) -> str:
        if self.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for", "")
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        return request.client.host if request.client else "unknown"
