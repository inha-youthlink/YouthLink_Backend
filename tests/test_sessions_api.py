import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.sessions import get_chat_service
from app.main import app
from app.repositories.chat_repository import RoomRecord
from app.schemas.analysis import Category
from app.schemas.chat import MessageItem
from app.schemas.pipeline import PolicyItem, Profile
from app.schemas.policy_info import ApplicationStatus
from tests.conftest import (
    CLIENT_ID,
    CREATED_AT,
    HEADERS,
    OTHER_CLIENT_ID,
    PROFILE,
    FakeChatRepository,
    make_service,
)


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def client(repo):
    app.dependency_overrides[get_chat_service] = lambda: make_service(repo)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create(client, headers=HEADERS, profile=None) -> dict:
    response = client.post("/api/sessions", json={"profile": profile or PROFILE}, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_create_session(client, repo):
    response = client.post("/api/sessions", json={"profile": PROFILE}, headers=HEADERS)

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    # 첫 질문 전 제목, 한국 시각
    assert body["title"] == "새 대화"
    assert body["createdAt"] == "2026-10-25T12:00:00+09:00"
    # 계약 형식으로 되돌려 줌
    assert body["profile"] == PROFILE
    # 저장은 DB 코드, 연소득은 원 단위
    assert repo.rooms[uuid.UUID(body["id"])].profile == Profile(
        age=27,
        region_code="11680",
        marriage_status_code="0055002",
        annual_income=30_000_000,
        major_code="0011005",
        job_code="0013003",
        school_code="0049007",
        special_target_code="0014010",
    )


# 과도기 호환: v0.3 프론트가 보내는 세션 분야는 무시하고, 시도 enum은 "○○ 전체" 코드로 받음
def test_create_session_accepts_v03_request(client):
    body = _create(client, profile={**PROFILE, "region": "SEOUL"})
    response = client.post(
        "/api/sessions", json={"category": "HOUSING", "profile": {**PROFILE, "region": "GYEONGGI"}}, headers=HEADERS
    )

    assert body["profile"]["region"] == "11000"
    assert response.status_code == 201
    assert response.json()["profile"]["region"] == "41000"


@pytest.mark.parametrize("region", ["11680", "11000", "41110", "41113"])
def test_create_session_region_codes(client, region):
    assert _create(client, profile={**PROFILE, "region": region})["profile"]["region"] == region


@pytest.mark.parametrize("region", ["99999", "11999", "1168", "강남구"])
def test_create_session_rejects_unknown_region(client, region):
    response = client.post("/api/sessions", json={"profile": {**PROFILE, "region": region}}, headers=HEADERS)

    assert response.status_code == 400
    assert response.json() == {
        "code": "VALIDATION_FAILED",
        "message": "입력값을 확인해 주세요.",
        "details": [{"field": "profile.region", "reason": "지원하지 않는 지역 코드입니다."}],
    }


@pytest.mark.parametrize(
    ("profile", "detail"),
    [
        ({**PROFILE, "age": 14}, {"field": "profile.age", "reason": "15 이상이어야 합니다."}),
        ({**PROFILE, "age": 40}, {"field": "profile.age", "reason": "39 이하여야 합니다."}),
        ({**PROFILE, "annualIncome": -1}, {"field": "profile.annualIncome", "reason": "0 이상이어야 합니다."}),
        ({**PROFILE, "major": "LAW"}, {"field": "profile.major", "reason": "허용된 값이 아닙니다."}),
        (
            {key: value for key, value in PROFILE.items() if key != "specialField"},
            {"field": "profile.specialField", "reason": "필수 항목입니다."},
        ),
    ],
)
def test_create_session_validation(client, profile, detail):
    response = client.post("/api/sessions", json={"profile": profile}, headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["details"] == [detail]


def test_create_session_requires_profile(client):
    response = client.post("/api/sessions", json={}, headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["details"] == [{"field": "profile", "reason": "필수 항목입니다."}]


# 8주차 임시 형식(DB 코드, 모두 선택)으로 만든 세션도 목록이 깨지지 않게 빈 값은 null
def test_old_session_profile_is_nullable(client, repo):
    room_id = uuid.uuid4()
    repo.rooms[room_id] = RoomRecord(room_id, CLIENT_ID, None, CREATED_AT, Profile(age=27))

    response = client.get("/api/sessions", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["profile"] == {
        "region": None, "maritalStatus": None, "age": 27, "annualIncome": None,
        "education": None, "major": None, "employmentStatus": None, "specialField": None,
    }


def test_requires_client_id(client):
    response = client.get("/api/sessions")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_CLIENT_ID"


def test_list_sessions_only_mine_newest_first(client):
    first = _create(client)
    second = _create(client)
    _create(client, headers={"X-Client-Id": OTHER_CLIENT_ID})

    response = client.get("/api/sessions", headers=HEADERS)

    assert [session["id"] for session in response.json()] == [second["id"], first["id"]]


def test_list_sessions_empty(client):
    response = client.get("/api/sessions", headers=HEADERS)

    assert response.json() == []


def test_get_session(client, repo):
    created = _create(client)
    room_id = uuid.UUID(created["id"])
    room = repo.rooms[room_id]
    repo.rooms[room_id] = RoomRecord(room.room_id, room.user_id, "서울 월세 지원", room.created_at, room.profile)

    response = client.get(f"/api/sessions/{created['id']}", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["title"] == "서울 월세 지원"


@pytest.mark.parametrize("path", ["", "/messages"])
def test_other_users_session_is_hidden(client, path):
    created = _create(client, headers={"X-Client-Id": OTHER_CLIENT_ID})

    response = client.get(f"/api/sessions/{created['id']}{path}", headers=HEADERS)

    assert response.status_code == 404
    assert response.json() == {"code": "SESSION_NOT_FOUND", "message": "세션을 찾을 수 없습니다."}


@pytest.mark.parametrize("session_id", ["not-a-uuid", str(uuid.uuid4())])
def test_unknown_session_id(client, session_id):
    response = client.get(f"/api/sessions/{session_id}", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["code"] == "SESSION_NOT_FOUND"


def test_list_messages(client, repo):
    created = _create(client)
    room_id = uuid.UUID(created["id"])
    repo.messages[room_id] = [
        MessageItem(message_id=11, role="user", content="질문1", created_at=CREATED_AT),
        MessageItem(message_id=12, role="assistant", content="답변1", created_at=CREATED_AT),
        MessageItem(message_id=13, role="system", content="내부", created_at=CREATED_AT),
        MessageItem(message_id=14, role="user", content="질문2", created_at=CREATED_AT),
        MessageItem(message_id=15, role="assistant", content="답변2", created_at=CREATED_AT),
    ]
    repo.refs[12] = [
        PolicyItem(
            policy_no="P-1",
            policy_name="청년 월세 지원",
            application_start_date=date(2026, 10, 1),
            application_end_date=date(2026, 11, 30),
            application_url="https://example.com",
            score=0.8,
            application_status=ApplicationStatus.OPEN,
            supervising_org_name="서울특별시",
            region_label="서울특별시",
            category=Category.WELFARE_CULTURE,
        )
    ]

    response = client.get(f"/api/sessions/{created['id']}/messages", headers=HEADERS)

    messages = response.json()
    assert [message["id"] for message in messages] == ["11", "12", "14", "15"]
    assert messages[0] == {
        "id": "11",
        "clientMessageId": None,
        "role": "user",
        "content": "질문1",
        "policies": None,
        "createdAt": "2026-10-25T12:00:00+09:00",
    }
    # 계약 형식의 카드. 관련도 점수 같은 내부 값은 내보내지 않음
    assert messages[1]["policies"] == [
        {
            "id": "P-1",
            "title": "청년 월세 지원",
            "category": "WELFARE",
            "applyStart": "2026-10-01",
            "applyEnd": "2026-11-30",
            "status": "OPEN",
            "regionLabel": "서울특별시",
            "region": "SEOUL",
            "organization": "서울특별시",
            "sourceUrl": "https://example.com",
        }
    ]
    assert messages[3]["policies"] == []


def test_responses_are_not_cached(client):
    created = _create(client)

    for path in ["/api/sessions", f"/api/sessions/{created['id']}"]:
        assert client.get(path, headers=HEADERS).headers["cache-control"] == "no-store"


def test_rooms_api_removed(client):
    response = client.get("/api/rooms", params={"user_id": "u"})

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
