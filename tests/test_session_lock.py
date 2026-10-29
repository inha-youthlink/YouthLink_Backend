from app.core.session_lock import SessionLocks


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_one_holder_per_key():
    locks = SessionLocks()

    assert locks.acquire("a")
    assert not locks.acquire("a")
    assert locks.acquire("b")
    locks.release("a")
    assert locks.acquire("a")


# 해제 코드가 돌지 못해 남은 잠금은 일정 시간이 지나면 다시 받음
def test_stale_lock_expires():
    clock = Clock()
    locks = SessionLocks(ttl=300, clock=clock)
    locks.acquire("a")

    clock.now = 299
    assert not locks.acquire("a")
    clock.now = 300
    assert locks.acquire("a")
