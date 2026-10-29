from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# 질문은 분석·답변 생성 입력에 그대로 들어가 길이가 곧 토큰·응답 시간이라 상한을 둠
QUESTION_MAX_CHARS = 1000


class MessageItem(BaseModel):
    message_id: int
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    # 질문에만 있는 재시도 키 (계약 R6)
    client_message_id: UUID | None = None
    created_at: datetime
