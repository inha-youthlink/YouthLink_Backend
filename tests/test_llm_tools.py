from types import SimpleNamespace

from pydantic import BaseModel

from app.llm.client import TOOL_LIMIT_OUTPUT, LLMClient, ToolCall, ToolLoopResult


class Answer(BaseModel):
    text: str


class Item:
    def __init__(self, type: str, **fields) -> None:
        self.type = type
        self._fields = {"type": type, **fields}
        for key, value in fields.items():
            setattr(self, key, value)

    def model_dump(self, exclude_none: bool = False, exclude: set | None = None) -> dict:
        return {k: v for k, v in self._fields.items() if k not in (exclude or set())}


def _call(call_id: str, arguments: str = '{"q": "월세"}', name: str = "search") -> Item:
    return Item("function_call", name=name, arguments=arguments, call_id=call_id, parsed_arguments=None)


def _response(*output, parsed=None, tokens=(10, 2)):
    usage = SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1])
    return SimpleNamespace(output=list(output), output_parsed=parsed, usage=usage)


class FakeResponses:
    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.requests: list[dict] = []

    async def parse(self, **kwargs):
        self.requests.append({**kwargs, "input": list(kwargs["input"])})
        return self._responses.pop(0)


class FakeOpenAI:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses
        self.options: dict = {}

    def with_options(self, **options):
        self.options = options
        return self


def _client(*responses) -> tuple[LLMClient, FakeResponses, FakeOpenAI]:
    fake = FakeResponses(*responses)
    openai = FakeOpenAI(fake)
    return LLMClient(openai, openai, "embed", "chat-model", "analysis-model"), fake, openai


async def _run(client: LLMClient, executed: list, max_calls: int = 3) -> list:
    async def execute(name, arguments):
        executed.append((name, arguments))
        return f"결과 {len(executed)}"

    messages = [{"role": "user", "content": "주거"}]
    return [
        event async for event in client.run_tools(messages, [{"name": "search"}], execute, Answer,
                                                  max_calls=max_calls, timeout=30)
    ]


async def test_answer_without_tools():
    client, fake, openai = _client(_response(Item("message"), parsed=Answer(text="안녕하세요")))
    executed: list = []
    events = await _run(client, executed)
    assert executed == []
    assert events == [ToolLoopResult(Answer(text="안녕하세요"), 10, 2, 1, [])]
    assert fake.requests[0]["tool_choice"] == "auto"
    assert fake.requests[0]["text_format"] is Answer
    assert openai.options == {"timeout": 30, "max_retries": 0}


# 도구 실행 직전에 ToolCall을 내보내고, 결과와 추론 항목을 붙여 다시 부름. 토큰은 왕복의 합
async def test_tool_call_then_answer():
    reasoning = Item("reasoning", id="rs_1")
    client, fake, _ = _client(
        _response(reasoning, _call("c1")),
        _response(Item("message"), parsed=Answer(text="찾았어요"), tokens=(30, 5)),
    )
    executed: list = []
    events = await _run(client, executed)
    assert events[0] == ToolCall("search", {"q": "월세"})
    assert events[1] == ToolLoopResult(Answer(text="찾았어요"), 40, 7, 2, [ToolCall("search", {"q": "월세"})])
    assert executed == [("search", {"q": "월세"})]
    second = fake.requests[1]["input"]
    assert second[1] == {"type": "reasoning", "id": "rs_1"}
    assert "parsed_arguments" not in second[2]
    assert second[3] == {"type": "function_call_output", "call_id": "c1", "output": "결과 1"}


# 상한을 넘는 호출은 실행하지 않고 결과만 붙이며, 다음 요청은 도구를 끔
async def test_limit_closes_tools():
    client, fake, _ = _client(
        _response(_call("c1")),
        _response(_call("c2"), _call("c3")),
        _response(Item("message"), parsed=Answer(text="끝")),
    )
    executed: list = []
    events = await _run(client, executed, max_calls=2)
    assert len(executed) == 2
    assert [request["tool_choice"] for request in fake.requests] == ["auto", "auto", "none"]
    last_input = fake.requests[2]["input"]
    assert last_input[-1] == {"type": "function_call_output", "call_id": "c3", "output": TOOL_LIMIT_OUTPUT}
    assert events[-1].rounds == 3 and len(events[-1].calls) == 2
    assert sum(isinstance(event, ToolCall) for event in events) == 2


# 도구를 끈 요청에도 모델이 호출을 내면 더 돌지 않고 그 응답으로 끝냄
async def test_call_after_closing_ends_loop():
    client, _, _ = _client(_response(_call("c1")), _response(_call("c2"), parsed=None))
    executed: list = []
    events = await _run(client, executed, max_calls=1)
    assert len(executed) == 1
    assert events[-1].parsed is None


async def test_broken_arguments_become_empty():
    client, _, _ = _client(_response(_call("c1", arguments="{broken")), _response(parsed=Answer(text="x")))
    executed: list = []
    await _run(client, executed)
    assert executed == [("search", {})]
