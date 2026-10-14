from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # .env.example을 그대로 복사해도 빈 값은 기본값을 사용함
        env_ignore_empty=True,
    )

    # App
    app_name: str = "YouthLink RAG"
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
    top_k: int = Field(default=10, ge=1)

    # Conversation
    history_turns: int = Field(default=3, ge=0)

    # Analysis
    analysis_model: str | None = None
    analysis_answer_max_chars: int = Field(default=300, ge=0)
    # 분석은 실패해도 원래 질문으로 진행하므로 답변 호출보다 짧게 두고 재시도하지 않음
    analysis_timeout: float = Field(default=10.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
