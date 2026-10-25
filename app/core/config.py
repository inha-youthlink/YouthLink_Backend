from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # .env.example을 그대로 복사해도 빈 값은 기본값을 사용함
        env_ignore_empty=True,
        # 설정 오류 로그에 API 키·DB 비밀번호가 찍히지 않게 입력값을 숨김
        hide_input_in_errors=True,
    )

    # App
    app_name: str = "YouthLink Backend"
    app_env: Literal["local", "dev", "prod"] = "local"
    debug: bool = False

    # OpenAI
    openai_api_key: SecretStr
    openai_timeout: float = 30.0
    openai_max_retries: int = 2
    embedding_model: str = "text-embedding-3-small"
    chat_model: str = "gpt-6-luna"

    # PostgreSQL
    database_url: SecretStr

    # Retrieval
    retriever: str = "vector"
    # 청크 단위라 10이면 정책이 6~8개뿐이어서 넉넉히 가져옴
    top_k: int = Field(default=30, ge=1)
    card_candidates: int = Field(default=6, ge=2)

    # Conversation
    history_turns: int = Field(default=3, ge=0)

    # Analysis
    analysis_model: str | None = None
    analysis_answer_max_chars: int = Field(default=300, ge=0)
    # 분석은 실패해도 원래 질문으로 진행하므로 답변 호출보다 짧게 두고 재시도하지 않음
    analysis_timeout: float = Field(default=10.0, gt=0)

    # Streaming
    # 프록시가 조용한 연결을 끊지 않게 보내는 간격 (계약 R4)
    sse_ping_interval: float = Field(default=15.0, gt=0)
    # 채팅 뼈대의 대기·길이 (계약 §5.4 검증 조건). 연결을 오래 잡아 검증이 끝나면 줄임
    chat_skeleton_first_delay: float = Field(default=15.0, ge=0)
    chat_skeleton_tokens: int = Field(default=90, ge=1)
    chat_skeleton_token_interval: float = Field(default=1.0, ge=0)

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    # 공개 주소로 열리는 운영 서버가 위험한 설정으로 뜨지 않게 시작할 때 막음
    @model_validator(mode="after")
    def _check_prod(self) -> "Settings":
        if self.is_prod and self.debug:
            raise ValueError("APP_ENV=prod에서는 DEBUG를 켤 수 없음 (오류 응답에 내부 정보가 나감)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
