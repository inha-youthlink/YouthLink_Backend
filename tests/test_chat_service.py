import uuid

import pytest
from pydantic import ValidationError

from app.schemas.chat import QUESTION_MAX_CHARS
from app.schemas.session import ChatRequest
from app.services.chat_service import ROOM_TITLE_MAX_CHARS, _room_title

KEY = uuid.uuid4()


def test_room_title_keeps_short_question():
    assert _room_title("서울 주거 지원 정책 알려줘") == "서울 주거 지원 정책 알려줘"


# 목록 한 줄에 맞게 줄바꿈·연속 공백을 합치고 길면 잘라 표시함
def test_room_title_truncates_long_question():
    question = "이번에 서울로 첫 직장 잡아서\n올라오게   됐는데요, 회사 근처 원룸 알아보니까 월세가 비싸요"

    title = _room_title(question)

    assert "\n" not in title and "  " not in title
    assert title.endswith("…")
    assert len(title) <= ROOM_TITLE_MAX_CHARS + 1


def test_question_length_limit():
    ChatRequest(client_message_id=KEY, content="가" * QUESTION_MAX_CHARS)

    with pytest.raises(ValidationError):
        ChatRequest(client_message_id=KEY, content="가" * (QUESTION_MAX_CHARS + 1))


# 공백만 있는 질문은 막고, 앞뒤 공백은 지운 값으로 저장·재시도 비교를 함
def test_question_is_stripped():
    assert ChatRequest(client_message_id=KEY, content="  서울 월세\n ").content == "서울 월세"

    with pytest.raises(ValidationError):
        ChatRequest(client_message_id=KEY, content=" \n\t ")
