from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 추측으로 맞힐 수 없는 길이. secrets.token_urlsafe(32)는 43자
INTERNAL_KEY_MIN_CHARS = 32


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

    # Security
    # /internal 호출에 필요한 키. 비어 있으면 검사하지 않음 (로컬 개발·테스트)
    internal_api_key: SecretStr | None = None
    # Vercel이 /api 요청에 붙여 보내는 비밀 헤더(X-Origin-Secret) 값. 비어 있으면 검사하지 않음
    # 켜면 EC2 주소로 직접 오는 공개 API 요청을 막고, X-Forwarded-For도 꾸밀 수 없게 됨
    origin_secret: SecretStr | None = None
    # 큰 본문으로 메모리를 쓰게 하는 요청을 막음. 질문 1000자·이전 대화가 들어가도 충분한 크기
    max_body_bytes: int = Field(default=256 * 1024, ge=0)

    # Rate limit (0이면 끔, 시연 전에 환경 변수로 조절)
    # 운영은 Vercel 뒤라 X-Forwarded-For의 첫 값을 접속 IP로 봄. 직접 접속하면 꾸밀 수 있어 하루 LLM 상한이 함께 막음
    trust_proxy_headers: bool = True
    # IP 기준은 시연장처럼 여러 사람이 같은 와이파이(같은 IP)를 써도 걸리지 않을 값. 한 사람은 X-Client-Id 기준이 막음
    rate_limit_per_minute: int = Field(default=300, ge=0)
    chat_rate_limit_per_minute: int = Field(default=60, ge=0)
    chat_client_rate_limit_per_minute: int = Field(default=10, ge=0)
    # 같은 와이파이의 여러 사람이 써도 시연 하루에 닿지 않을 값. 넘으면 다음 날(한국 자정)까지 LLM 호출을 막음
    llm_daily_limit: int = Field(default=1000, ge=0)

    # OpenAI
    openai_api_key: SecretStr
    # 비우면 OpenAI에 바로 붙음. 운영은 Bedrock 게이트웨이 주소
    openai_base_url: str | None = None
    openai_timeout: float = 30.0
    openai_max_retries: int = 2
    embedding_model: str = "bedrock-titan-embed-v2"
    chat_model: str = "bedrock-gpt-5.6-luna"

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
        if not self.is_prod:
            return self
        if self.debug:
            raise ValueError("APP_ENV=prod에서는 DEBUG를 켤 수 없음 (오류 응답에 내부 정보가 나감)")
        key = self.internal_api_key.get_secret_value() if self.internal_api_key else ""
        if len(key) < INTERNAL_KEY_MIN_CHARS:
            raise ValueError(
                f"APP_ENV=prod에는 {INTERNAL_KEY_MIN_CHARS}자 이상의 INTERNAL_API_KEY가 필요함 (/internal이 LLM을 부름)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
