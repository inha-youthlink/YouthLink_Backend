from dataclasses import dataclass

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import Settings


@dataclass(frozen=True)
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class ParsedResult[T: BaseModel]:
    # 모델이 응답을 거부하면 SDK가 None을 돌려주므로 호출 측이 실패로 처리함
    parsed: T | None
    prompt_tokens: int
    completion_tokens: int


class LLMClient:
    def __init__(
        self,
        client: AsyncOpenAI,
        analysis_client: AsyncOpenAI,
        embedding_model: str,
        chat_model: str,
        analysis_model: str,
    ) -> None:
        self._client = client
        self._analysis_client = analysis_client
        self.embedding_model = embedding_model
        self.chat_model = chat_model
        self.analysis_model = analysis_model

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMClient":
        client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout=settings.openai_timeout,
            max_retries=settings.openai_max_retries,
        )
        # 분석은 실패해도 원래 질문으로 진행하므로 재시도로 기다리지 않고 바로 실패시킴
        analysis_client = client.with_options(timeout=settings.analysis_timeout, max_retries=0)
        return cls(
            client,
            analysis_client,
            settings.embedding_model,
            settings.chat_model,
            settings.analysis_model or settings.chat_model,
        )

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(
            model=self.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    async def chat(self, messages: list[dict[str, str]]) -> ChatResult:
        response = await self._client.responses.create(
            model=self.chat_model,
            input=messages,
        )
        usage = response.usage
        # Responses API의 input/output 토큰을 trace 형식(prompt/completion)에 맞춤
        return ChatResult(
            text=response.output_text,
            prompt_tokens=usage.input_tokens if usage else 0,
            completion_tokens=usage.output_tokens if usage else 0,
        )

    async def analyze[T: BaseModel](
        self, messages: list[dict[str, str]], text_format: type[T]
    ) -> ParsedResult[T]:
        response = await self._analysis_client.responses.parse(
            model=self.analysis_model,
            input=messages,
            text_format=text_format,
        )
        usage = response.usage
        return ParsedResult(
            parsed=response.output_parsed,
            prompt_tokens=usage.input_tokens if usage else 0,
            completion_tokens=usage.output_tokens if usage else 0,
        )

    async def close(self) -> None:
        await self._client.close()
