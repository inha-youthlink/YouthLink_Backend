from types import SimpleNamespace

import pytest

from app.llm.client import BinaryEmbeddingError, ChatUsage, LLMClient, LLMStreamError


class FakeStream:
    def __init__(self, events) -> None:
        self._events = events
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event


class FakeResponses:
    def __init__(self, stream: FakeStream) -> None:
        self.stream = stream
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.stream


def _client(*events) -> tuple[LLMClient, FakeResponses]:
    responses = FakeResponses(FakeStream(events))
    openai = SimpleNamespace(responses=responses)
    return LLMClient(openai, openai, "embed", "chat-model", "analysis-model"), responses


def _delta(text: str):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


USAGE = SimpleNamespace(input_tokens=31, output_tokens=104)


def _done(kind: str = "response.completed", usage=USAGE):
    return SimpleNamespace(type=kind, response=SimpleNamespace(usage=usage))


async def _collect(client: LLMClient) -> list:
    return [piece async for piece in client.chat_stream([{"role": "user", "content": "q"}])]


# 게이트웨이에서 확인한 이벤트 순서: 조각 사이에 다른 이벤트가 섞여도 글자와 토큰 수만 꺼냄
async def test_chat_stream_yields_deltas_then_usage():
    client, responses = _client(
        SimpleNamespace(type="response.created"), _delta("청년 "), _delta("월세"),
        SimpleNamespace(type="response.output_text.done"), _done(),
    )

    assert await _collect(client) == ["청년 ", "월세", ChatUsage(prompt_tokens=31, completion_tokens=104)]
    assert (responses.kwargs["model"], responses.kwargs["stream"]) == ("chat-model", True)
    assert responses.stream.closed


# 길이 상한으로 끊긴 응답도 한 번에 받을 때처럼 받은 글자까지 씀
async def test_incomplete_response_keeps_text():
    client, _ = _client(_delta("일부"), _done("response.incomplete", usage=None))

    assert await _collect(client) == ["일부", ChatUsage(prompt_tokens=0, completion_tokens=0)]


@pytest.mark.parametrize("kind", ["response.failed", "error"])
async def test_failed_stream_raises_openai_error(kind):
    client, responses = _client(_delta("일부"), SimpleNamespace(type=kind))

    with pytest.raises(LLMStreamError):
        await _collect(client)
    assert responses.stream.closed


# 소비하는 쪽이 중간에 멈추면(연결 끊김) 스트림이 닫힘
async def test_stream_closed_when_consumer_stops():
    client, responses = _client(_delta("가"), _delta("나"), _done())

    pieces = client.chat_stream([{"role": "user", "content": "q"}])
    assert await anext(pieces) == "가"
    await pieces.aclose()

    assert responses.stream.closed


class FakeEmbeddings:
    def __init__(self, vector) -> None:
        self.vector = vector
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(data=[SimpleNamespace(embedding=self.vector)])


def _embed_client(vector) -> tuple[LLMClient, FakeEmbeddings]:
    embeddings = FakeEmbeddings(vector)
    openai = SimpleNamespace(embeddings=embeddings)
    return LLMClient(openai, openai, "embed-model", "chat-model", "analysis-model"), embeddings


# 빼면 SDK가 base64를 보내고 게이트웨이가 이진(0/1) 벡터를 돌려줌
async def test_embed_asks_for_float_encoding():
    client, embeddings = _embed_client([0.031, -0.0127, 0.5])

    vector = await client.embed("질문")

    assert vector == [0.031, -0.0127, 0.5]
    assert embeddings.kwargs == {"model": "embed-model", "input": "질문", "encoding_format": "float"}


@pytest.mark.parametrize("vector", [[1, 0, 0, 1], [0.0, 1.0, 1.0]])
async def test_embed_rejects_binary_vector(vector):
    client, _ = _embed_client(vector)

    with pytest.raises(BinaryEmbeddingError):
        await client.embed("질문")


class FakeChatResponses:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text="요약", usage=SimpleNamespace(input_tokens=12, output_tokens=3))


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeChatResponses()
        self.options: list[dict] = []

    def with_options(self, **options):
        self.options.append(options)
        return self


async def test_chat_without_options_uses_model_defaults():
    openai = FakeOpenAI()
    client = LLMClient(openai, openai, "embed", "chat-model", "analysis-model")

    result = await client.chat([{"role": "user", "content": "q"}])

    assert (result.text, result.prompt_tokens, result.completion_tokens) == ("요약", 12, 3)
    assert openai.responses.kwargs == {"model": "chat-model", "input": [{"role": "user", "content": "q"}]}
    assert openai.options == []


# 대화 요약: 출력 상한, 낮은 추론, 재시도 없음
async def test_chat_passes_short_answer_options():
    openai = FakeOpenAI()
    client = LLMClient(openai, openai, "embed", "chat-model", "analysis-model")

    await client.chat([], max_output_tokens=1000, reasoning_effort="low", max_retries=0)

    assert openai.responses.kwargs["max_output_tokens"] == 1000
    assert openai.responses.kwargs["reasoning"] == {"effort": "low"}
    assert openai.options == [{"max_retries": 0}]
