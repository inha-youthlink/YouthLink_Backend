from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.repositories.scrap_repository import AddResult, ScrapRepository
from app.schemas.policy_info import ALWAYS_OPEN_CODE, CLOSED_CODE

TODAY = date(2026, 11, 1)
PERIOD_CODE = "0057001"
USER, OTHER_USER = "scrap-user", "other-user"
GONE_AT = datetime(2026, 10, 20, tzinfo=UTC)


def _period(start, end) -> dict:
    return {"application_period_type_code": PERIOD_CODE, "application_start_date": start, "application_end_date": end}


POLICIES = {
    "SC-OPEN-LATE": _period(date(2026, 10, 1), date(2026, 12, 31)),
    "SC-OPEN-SOON": _period(date(2026, 10, 1), date(2026, 11, 5)),
    # 마감일이 같은 진행 중 두 건: 최근에 스크랩한 것 먼저
    "SC-SAME-A": _period(date(2026, 10, 1), date(2026, 11, 20)),
    "SC-SAME-B": _period(date(2026, 10, 1), date(2026, 11, 20)),
    "SC-UPCOMING": _period(date(2026, 11, 10), date(2026, 11, 30)),
    "SC-ALWAYS": {"application_period_type_code": ALWAYS_OPEN_CODE},
    "SC-UNKNOWN": {"application_period_type_code": PERIOD_CODE},
    "SC-CLOSED": {"application_period_type_code": CLOSED_CODE},
    "SC-GONE": {**_period(date(2026, 10, 1), date(2026, 11, 3)), "is_active": False, "deactivated_at": GONE_AT},
}
# 스크랩 순서(먼저 넣은 것이 오래된 스크랩)
SCRAP_ORDER = ["SC-CLOSED", "SC-SAME-A", "SC-ALWAYS", "SC-OPEN-LATE", "SC-UNKNOWN", "SC-GONE",
               "SC-UPCOMING", "SC-SAME-B", "SC-OPEN-SOON"]
EXPECTED = ["SC-OPEN-SOON", "SC-SAME-B", "SC-SAME-A", "SC-OPEN-LATE", "SC-UPCOMING", "SC-ALWAYS",
            "SC-UNKNOWN", "SC-CLOSED"]


@pytest.fixture
async def repository(session_factory):
    async with session_factory() as session, session.begin():
        for policy_no, columns in POLICIES.items():
            columns = {"policy_name": policy_no, **columns}
            names = ["policy_no", *columns]
            await session.execute(
                text(f"INSERT INTO policy ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})"),
                {"policy_no": policy_no, **columns},
            )
    yield ScrapRepository(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM policy_scrap"))
        await session.execute(text("DELETE FROM policy WHERE policy_no LIKE 'SC-%'"))


async def _scrap_in_order(session_factory, user: str, policy_nos: list[str]) -> None:
    started = datetime(2026, 10, 1, tzinfo=UTC)
    async with session_factory() as session, session.begin():
        for index, policy_no in enumerate(policy_nos):
            await session.execute(
                text("INSERT INTO policy_scrap (user_id, policy_no, created_at) VALUES (:u, :p, :at)"),
                {"u": user, "p": policy_no, "at": started + timedelta(hours=index)},
            )


async def test_list_order_and_inactive_excluded(repository, session_factory):
    await _scrap_in_order(session_factory, USER, SCRAP_ORDER)

    scraps = await repository.list_scraps(USER, TODAY)

    # 진행 중(마감 임박, 같은 날짜는 최근 스크랩 먼저) → 예정 → 상시 → 정보 없음 → 마감. 비활성은 빠짐
    assert [s.record.policy_no for s in scraps] == EXPECTED
    assert scraps[0].created_at == datetime(2026, 10, 1, 8, tzinfo=UTC)


async def test_list_only_own(repository, session_factory):
    await _scrap_in_order(session_factory, OTHER_USER, ["SC-ALWAYS"])

    assert await repository.list_scraps(USER, TODAY) == []


async def test_add_created_then_existing(repository):
    assert await repository.add_scrap(USER, "SC-ALWAYS") is AddResult.CREATED
    first = await repository.get_scrap(USER, "SC-ALWAYS", TODAY)

    assert await repository.add_scrap(USER, "SC-ALWAYS") is AddResult.EXISTS
    second = await repository.get_scrap(USER, "SC-ALWAYS", TODAY)

    # 다시 눌러도 처음 스크랩한 시각 그대로
    assert first is not None and first == second
    assert first.record.policy_name == "SC-ALWAYS"


async def test_add_unknown_or_inactive_policy(repository):
    assert await repository.add_scrap(USER, "SC-NONE") is AddResult.POLICY_NOT_FOUND
    assert await repository.add_scrap(USER, "SC-GONE") is AddResult.POLICY_NOT_FOUND
    assert await repository.list_scraps(USER, TODAY) == []


async def test_inactive_scrap_kept_and_returns(repository, session_factory):
    await _scrap_in_order(session_factory, USER, ["SC-GONE"])
    assert await repository.get_scrap(USER, "SC-GONE", TODAY) is None

    # 정책이 다시 활성이 되면 목록에 돌아옴 (행은 지우지 않음)
    async with session_factory() as session, session.begin():
        await session.execute(text("UPDATE policy SET is_active = TRUE, deactivated_at = NULL WHERE policy_no = 'SC-GONE'"))

    assert [s.record.policy_no for s in await repository.list_scraps(USER, TODAY)] == ["SC-GONE"]


async def test_remove_idempotent_and_own_only(repository, session_factory):
    await _scrap_in_order(session_factory, USER, ["SC-ALWAYS", "SC-GONE"])
    await _scrap_in_order(session_factory, OTHER_USER, ["SC-ALWAYS"])

    await repository.remove_scrap(USER, "SC-ALWAYS")
    await repository.remove_scrap(USER, "SC-ALWAYS")
    # 비활성 정책도 지울 수 있음
    await repository.remove_scrap(USER, "SC-GONE")

    async with session_factory() as session:
        rows = (await session.execute(text("SELECT user_id, policy_no FROM policy_scrap"))).all()
    assert [tuple(row) for row in rows] == [(OTHER_USER, "SC-ALWAYS")]
