from collections.abc import AsyncIterator
from dataclasses import dataclass

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel

from app.core.config import Settings


@dataclass(frozen=True)
class ChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int


# 스트림의 마지막 이벤트. 토큰 수는 응답이 끝나야 알 수 있음
@dataclass(frozen=True)
class ChatUsage:
    prompt_tokens: int
    completion_tokens: int


# 스트림 도중 실패도 한 번에 받을 때와 같은 오류(OpenAIError)로 처리되게 함
class LLMStreamError(OpenAIError):
    pass


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

    # 글자 조각(str)을 받는 대로 내보내고 마지막에 ChatUsage를 내보냄
    # 소비하는 쪽이 멈추면(연결 끊김) async with가 스트림을 닫아 생성도 멈춤
    async def chat_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str | ChatUsage]:
        stream = await self._client.responses.create(
            model=self.chat_model,
            input=messages,
            stream=True,
        )
        async with stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield event.delta
                # 길이 상한으로 끊긴 응답(incomplete)도 chat()처럼 받은 글자까지 답변으로 씀
                elif event.type in ("response.completed", "response.incomplete"):
                    usage = event.response.usage
                    yield ChatUsage(
                        prompt_tokens=usage.input_tokens if usage else 0,
                        completion_tokens=usage.output_tokens if usage else 0,
                    )
                elif event.type in ("response.failed", "error"):
                    raise LLMStreamError(f"답변 스트림 실패: {event.type}")

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
