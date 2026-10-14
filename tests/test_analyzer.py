import pytest
from openai import OpenAIError

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.llm.client import ParsedResult
from app.pipeline import analyzer
from app.pipeline.analyzer import FallbackReason
from app.pipeline.context import PipelineContext
from app.schemas.analysis import Intent, QueryAnalysis, StatusFilter, SubQuery
from app.schemas.pipeline import ConversationTurn, PipelineInput, PolicyRef

MONTHLY_RENT = PolicyRef(policy_no="P-RENT", policy_name="청년 월세 지원")
MOVING = PolicyRef(policy_no="P-MOVE", policy_name="청년 이사비 지원")
HOUSING_LOAN = PolicyRef(policy_no="P-LOAN", policy_name="청년 전세대출")


class FakeLLM:
    analysis_model = "fake-analysis-model"

    def __init__(self, result: ParsedResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.messages: list[dict[str, str]] = []

    async def analyze(self, messages, text_format):
        self.messages = messages
        if self._error is not None:
            raise self._error
        return self._result


def _sub(question: str, refs: list[str], intent: Intent = Intent.POLICY_DETAIL) -> SubQuery:
    return SubQuery(
        standalone_question=question, target_refs=refs, intent=intent, status_filter=StatusFilter.NONE
    )


def _parsed(*subs: SubQuery, is_new_topic: bool = False) -> ParsedResult:
    return ParsedResult(
        parsed=QueryAnalysis(sub_queries=list(subs), is_new_topic=is_new_topic),
        prompt_tokens=100,
        completion_tokens=20,
    )


def _ctx(inp: PipelineInput, llm: FakeLLM, answer_max_chars: int = 300) -> PipelineContext:
    settings = Settings(
        openai_api_key="test",
        database_url="postgresql+asyncpg://test@localhost/test",
        analysis_answer_max_chars=answer_max_chars,
    )
    deps = PipelineDeps(
        settings=settings, llm=llm, retriever=None, policy_repository=None, common_code_repository=None
    )
    return PipelineContext(input=inp, deps=deps, query=inp.question)


def _user_message(llm: FakeLLM) -> str:
    return llm.messages[1]["content"]


async def test_input_without_history():
    llm = FakeLLM(_parsed(_sub("서울 주거 지원 정책", [], Intent.POLICY_LIST)))

    await analyzer.analyze(_ctx(PipelineInput(question="서울 주거 지원 정책 알려줘"), llm))

    assert _user_message(llm) == "\n".join([
        "[최근 대화]", "없음",
        "[직전에 보여준 정책]", "없음",
        "[현재 대화 중인 정책]", "없음",
        "[질문]", "서울 주거 지원 정책 알려줘",
    ])
    assert llm.messages[0]["role"] == "system"


# 화면에 있는 현재 정책은 카드 번호로, 화면 밖 현재 정책만 C 표기로 나와야 함
async def test_input_labels_shown_and_current_policies():
    inp = PipelineInput(
        question="그거 서류는?",
        history=[
            ConversationTurn(question="예전 질문", answer="예전 답변", policies=[HOUSING_LOAN]),
            ConversationTurn(
                question="주거 지원 알려줘",
                answer="정책을 소개해 드릴게요.\n1. 청년 월세 지원은 월세를 지원해요.",
                policies=[MONTHLY_RENT, MOVING],
            ),
        ],
        current_policies=[MOVING, HOUSING_LOAN],
    )
    llm = FakeLLM(_parsed(_sub("청년 이사비 지원 서류", ["2"])))

    await analyzer.analyze(_ctx(inp, llm, answer_max_chars=13))

    assert _user_message(llm) == "\n".join([
        "[최근 대화]",
        "<턴 1>", "질문: 예전 질문", "답변: 예전 답변", "보여준 정책: 청년 전세대출",
        "<턴 2>", "질문: 주거 지원 알려줘", "답변: 정책을 소개해 드릴게요.…", "보여준 정책: 청년 월세 지원, 청년 이사비 지원",
        "[직전에 보여준 정책]", "1. 청년 월세 지원", "2. 청년 이사비 지원",
        "[현재 대화 중인 정책]", "2번과 같음", "C1. 청년 전세대출",
        "[질문]", "그거 서류는?",
    ])


async def test_refs_resolved_and_invalid_refs_dropped():
    inp = PipelineInput(
        question="질문",
        history=[ConversationTurn(question="q", answer="a", policies=[MONTHLY_RENT, MOVING])],
        current_policies=[HOUSING_LOAN],
    )
    # 없는 번호, 이름, 중복, 공백·소문자 표기를 섞음
    llm = FakeLLM(_parsed(_sub("질문", ["2", "4", "청년 월세 지원", " c1 ", "2"])))

    result = await analyzer.analyze(_ctx(inp, llm))

    sub = result.sub_queries[0]
    assert sub.target_refs == ["2", "4", "청년 월세 지원", " c1 ", "2"]
    assert sub.target_policy_nos == [MOVING.policy_no, HOUSING_LOAN.policy_no]
    assert result.fallback_reason is None
    assert (result.prompt_tokens, result.completion_tokens) == (100, 20)
    assert result.model == "fake-analysis-model"


async def test_search_query_uses_standalone_questions():
    single = await analyzer.analyze(_ctx(PipelineInput(question="두 번째 거 신청 방법은?"), FakeLLM(
        _parsed(_sub("청년 이사비 지원 신청 방법", []))
    )))
    multi = await analyzer.analyze(_ctx(PipelineInput(question="이사비 그건 서류? 월세랑 같이 돼?"), FakeLLM(
        _parsed(_sub("청년 이사비 지원 서류", []), _sub("청년 이사비 지원과 월세 지원 중복", []))
    )))

    assert single.search_query("두 번째 거 신청 방법은?") == "청년 이사비 지원 신청 방법"
    assert multi.search_query("원래 질문") == "청년 이사비 지원 서류 청년 이사비 지원과 월세 지원 중복"


async def test_sub_queries_limited():
    llm = FakeLLM(_parsed(*(_sub(f"질문{i}", []) for i in range(5))))

    result = await analyzer.analyze(_ctx(PipelineInput(question="질문"), llm))

    assert [sub.standalone_question for sub in result.sub_queries] == ["질문0", "질문1", "질문2"]


async def test_llm_error_falls_back_to_original_question():
    llm = FakeLLM(error=OpenAIError("timeout"))

    result = await analyzer.analyze(_ctx(PipelineInput(question="원래 질문"), llm))

    assert result.fallback_reason == FallbackReason.LLM_ERROR
    assert result.sub_queries == []
    assert result.is_new_topic is False
    assert result.search_query("원래 질문") == "원래 질문"


@pytest.mark.parametrize("parsed", [None, QueryAnalysis(sub_queries=[], is_new_topic=True)])
async def test_empty_result_is_schema_error(parsed):
    llm = FakeLLM(ParsedResult(parsed=parsed, prompt_tokens=100, completion_tokens=5))

    result = await analyzer.analyze(_ctx(PipelineInput(question="원래 질문"), llm))

    assert result.fallback_reason == FallbackReason.SCHEMA_ERROR
    assert result.is_new_topic is False
    assert (result.prompt_tokens, result.completion_tokens) == (100, 5)
    assert result.search_query("원래 질문") == "원래 질문"
