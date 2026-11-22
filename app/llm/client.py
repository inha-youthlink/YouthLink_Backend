import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

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
    # 아래는 응답 속도 측정용. 게이트웨이가 주지 않으면 0·None
    # 프롬프트 캐시에 맞은 입력 토큰, 출력 전 추론 토큰
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    # 요청부터 첫 이벤트(응답 시작)·첫 글자까지 ms. 둘의 차이가 대부분 추론 시간
    first_event_ms: int | None = None
    first_text_ms: int | None = None


# 스트림 도중 실패도 한 번에 받을 때와 같은 오류(OpenAIError)로 처리되게 함
class LLMStreamError(OpenAIError):
    pass


@dataclass(frozen=True)
class ParsedResult[T: BaseModel]:
    # 모델이 응답을 거부하면 SDK가 None을 돌려주므로 호출 측이 실패로 처리함
    parsed: T | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0


# 문서 임베딩(ETL)은 실수라 이진 질문 벡터로 검색하면 결과가 틀어짐. 조용히 검색하지 않고 실패시킴
# 게이트웨이 쪽 문제라 다른 LLM 호출 실패와 같이 처리되게 OpenAIError로 둠 (/internal 503, 채팅 RAG_UPSTREAM_ERROR)
class BinaryEmbeddingError(OpenAIError):
    def __init__(self) -> None:
        super().__init__("임베딩 API가 이진(0/1) 벡터를 돌려줌 (encoding_format=float 확인)")


# 도구 루프가 도구를 실행하기 직전에 내보냄. 받는 쪽이 진행 문구(SSE progress)를 만듦
@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


# 도구 루프의 마지막 이벤트. 토큰은 모든 왕복의 합
@dataclass(frozen=True)
class ToolLoopResult[T: BaseModel]:
    parsed: T | None
    prompt_tokens: int
    completion_tokens: int
    rounds: int
    calls: list[ToolCall] = field(default_factory=list)


# 도구 이름과 인자(dict)를 받아 모델에 돌려줄 글을 만듦
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]

# 상한을 넘겨 실행하지 않은 호출에도 결과를 붙여야 다음 요청이 받아들여짐
TOOL_LIMIT_OUTPUT = "도구 호출 상한에 도달해 실행하지 않음. 지금까지의 결과로 답할 것"


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

    # encoding_format을 빼면 SDK가 "base64"를 보내고 게이트웨이가 Titan 이진 출력(0/1)으로 바꿈
    # 이진 벡터는 크기 정보가 사라져 검색 점수가 좁은 범위에 몰리므로 실수로 요청하고, 그래도 오면 멈춤
    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(
            model=self.embedding_model,
            input=text,
            encoding_format="float",
        )
        vector = response.data[0].embedding
        if vector and all(value in (0, 1) for value in vector):
            raise BinaryEmbeddingError
        return vector

    # 대화 요약처럼 짧은 답에는 출력 상한과 낮은 추론을 줘 기다림을 줄임. 주지 않으면 모델 기본값
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        max_retries: int | None = None,
    ) -> ChatResult:
        client = self._client if max_retries is None else self._client.with_options(max_retries=max_retries)
        options: dict[str, Any] = {}
        if max_output_tokens is not None:
            options["max_output_tokens"] = max_output_tokens
        if reasoning_effort is not None:
            options["reasoning"] = {"effort": reasoning_effort}
        response = await client.responses.create(
            model=self.chat_model,
            input=messages,
            **options,
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
    async def chat_stream(self, messages: list[dict[str, str]]) -> AsyncGenerator[str | ChatUsage, None]:
        started = perf_counter()
        first_event_ms: int | None = None
        first_text_ms: int | None = None
        stream = await self._client.responses.create(
            model=self.chat_model,
            input=messages,
            stream=True,
        )
        async with stream:
            async for event in stream:
                if first_event_ms is None:
                    first_event_ms = _elapsed_ms(started)
                if event.type == "response.output_text.delta":
                    if first_text_ms is None:
                        first_text_ms = _elapsed_ms(started)
                    yield event.delta
                # 길이 상한으로 끊긴 응답(incomplete)도 chat()처럼 받은 글자까지 답변으로 씀
                elif event.type in ("response.completed", "response.incomplete"):
                    usage = event.response.usage
                    cached_tokens, reasoning_tokens = _token_details(usage)
                    yield ChatUsage(
                        prompt_tokens=usage.input_tokens if usage else 0,
                        completion_tokens=usage.output_tokens if usage else 0,
                        cached_tokens=cached_tokens,
                        reasoning_tokens=reasoning_tokens,
                        first_event_ms=first_event_ms,
                        first_text_ms=first_text_ms,
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
        cached_tokens, reasoning_tokens = _token_details(usage)
        return ParsedResult(
            parsed=response.output_parsed,
            prompt_tokens=usage.input_tokens if usage else 0,
            completion_tokens=usage.output_tokens if usage else 0,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    # 답변 모델로 구조화 출력을 받음 (신청 가이드). 분석과 달리 입력이 길어 시간 제한을 따로 줌
    # 동기 요청이라 재시도하면 사용자가 시간 제한을 몇 번 기다릴 수 있어 한 번만 부름
    async def structured[T: BaseModel](
        self, messages: list[dict[str, str]], text_format: type[T], *, timeout: float
    ) -> ParsedResult[T]:
        client = self._client.with_options(timeout=timeout, max_retries=0)
        response = await client.responses.parse(
            model=self.chat_model,
            input=messages,
            text_format=text_format,
        )
        usage = response.usage
        return ParsedResult(
            parsed=response.output_parsed,
            prompt_tokens=usage.input_tokens if usage else 0,
            completion_tokens=usage.output_tokens if usage else 0,
        )

    # 도구 호출이 오면 실행해 결과를 붙이고 다시 부름. 도구 없이 답하면 그 구조화 출력이 결과
    # 상한에 닿으면 마지막 요청은 도구를 끄고(tool_choice=none) 답을 받아 왕복이 끝없이 늘지 않게 함
    # 재시도하면 시간 제한을 몇 번 기다릴 수 있어 한 번만 부름. 전체 시간 제한은 부르는 쪽이 검
    async def run_tools[T: BaseModel](
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        execute: ToolExecutor,
        text_format: type[T],
        *,
        max_calls: int,
        timeout: float,
    ) -> AsyncGenerator[ToolCall | ToolLoopResult[T], None]:
        client = self._client.with_options(timeout=timeout, max_retries=0)
        items: list[Any] = list(messages)
        calls: list[ToolCall] = []
        prompt_tokens = completion_tokens = rounds = 0
        while True:
            closing = len(calls) >= max_calls
            response = await client.responses.parse(
                model=self.chat_model,
                input=items,
                tools=tools,
                tool_choice="none" if closing else "auto",
                text_format=text_format,
            )
            rounds += 1
            if response.usage:
                prompt_tokens += response.usage.input_tokens
                completion_tokens += response.usage.output_tokens
            requested = [item for item in response.output if item.type == "function_call"]
            if not requested or closing:
                yield ToolLoopResult(response.output_parsed, prompt_tokens, completion_tokens, rounds, calls)
                return

            # 추론 항목도 함께 돌려줘야 모델이 이어서 생각함. 답 글(message)은 도구를 부르는 중이라 뺌
            items += [_as_input(item) for item in response.output if item.type != "message"]
            for request in requested:
                if len(calls) >= max_calls:
                    output = TOOL_LIMIT_OUTPUT
                else:
                    call = ToolCall(request.name, _arguments(request.arguments))
                    calls.append(call)
                    yield call
                    output = await execute(call.name, call.arguments)
                items.append({"type": "function_call_output", "call_id": request.call_id, "output": output})

    async def close(self) -> None:
        await self._client.close()


# parse가 붙이는 해석 결과(parsed_arguments)는 요청 형식에 없는 필드라 뺌
def _as_input(item: Any) -> dict[str, Any]:
    return item.model_dump(exclude_none=True, exclude={"parsed_arguments"})


# strict 스키마라 보통 JSON 객체지만, 깨진 인자는 빈 인자로 실행해 도구가 오류 글을 돌려주게 함
def _arguments(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


# 게이트웨이(Bedrock 경유)가 세부 토큰을 주지 않을 수 있어 없으면 0
def _token_details(usage: Any) -> tuple[int, int]:
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return (
        getattr(input_details, "cached_tokens", None) or 0,
        getattr(output_details, "reasoning_tokens", None) or 0,
    )
