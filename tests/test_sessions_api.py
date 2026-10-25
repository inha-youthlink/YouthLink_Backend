import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.sessions import get_chat_service
from app.main import app
from app.repositories.chat_repository import RoomRecord
from app.schemas.chat import MessageItem
from app.schemas.pipeline import PolicyItem, Profile
from app.schemas.policy_info import ApplicationStatus
from app.services.chat_service import ChatService

CLIENT_ID = "6f1c2b9e-3d4a-4c8b-9f2e-1a2b3c4d5e6f"
OTHER_CLIENT_ID = "0b6a4b1e-8d1f-4f5e-a2c3-9e8d7c6b5a40"
HEADERS = {"X-Client-Id": CLIENT_ID}
CREATED_AT = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)


class FakeChatRepository:
    def __init__(self) -> None:
        self.rooms: dict[uuid.UUID, RoomRecord] = {}
        self.messages: dict[uuid.UUID, list[MessageItem]] = {}
        self.refs: dict[int, list[PolicyItem]] = {}

    async def create_room(self, user_id: str, profile: Profile) -> uuid.UUID:
        room_id = uuid.uuid4()
        created_at = CREATED_AT + timedelta(minutes=len(self.rooms))
        self.rooms[room_id] = RoomRecord(room_id, user_id, None, created_at, profile)
        return room_id

    async def get_room(self, room_id: uuid.UUID) -> RoomRecord | None:
        return self.rooms.get(room_id)

    async def list_rooms(self, user_id: str) -> list[RoomRecord]:
        rooms = [room for room in self.rooms.values() if room.user_id == user_id]
        return sorted(rooms, key=lambda room: room.created_at, reverse=True)

    async def list_messages(self, room_id: uuid.UUID) -> list[MessageItem]:
        return self.messages.get(room_id, [])

    async def list_policy_refs(self, message_ids: list[int]) -> dict[int, list[PolicyItem]]:
        return {message_id: self.refs[message_id] for message_id in message_ids if message_id in self.refs}


@pytest.fixture
def repo() -> FakeChatRepository:
    return FakeChatRepository()


@pytest.fixture
def client(repo):
    app.dependency_overrides[get_chat_service] = lambda: ChatService(repo, None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create(client, headers=HEADERS, profile=None) -> dict:
    response = client.post("/api/sessions", json={"profile": profile or {}}, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_create_session(client):
    response = client.post("/api/sessions", json={"profile": {"age": 27, "region_code": "11680"}}, headers=HEADERS)

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    # 첫 질문 전 제목, 한국 시각
    assert body["title"] == "새 대화"
    assert body["createdAt"] == "2026-10-25T12:00:00+09:00"
    assert body["profile"]["age"] == 27
    assert body["profile"]["region_code"] == "11680"


def test_create_session_without_body_profile(client):
    response = client.post("/api/sessions", json={}, headers=HEADERS)

    assert response.status_code == 201


def test_create_session_validation(client):
    response = client.post("/api/sessions", json={"profile": {"age": -1}}, headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["details"] == [{"field": "profile.age", "reason": "0 이상이어야 합니다."}]


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
            "applyStart": "2026-10-01",
            "applyEnd": "2026-11-30",
            "status": "OPEN",
            "regionLabel": "서울특별시",
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
