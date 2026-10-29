import uuid

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.llm.client import ChatUsage, ParsedResult
from app.pipeline.runner import AnswerToken, PolicyCards, run_pipeline, stream_pipeline
from app.repositories.policy_repository import PolicyRecord, RegionRow
from app.schemas.analysis import (
    Category,
    Intent,
    JobStatus,
    QueryAnalysis,
    SchoolLevel,
    StatusFilter,
    SubQuery,
)
from app.schemas.pipeline import (
    Chunk,
    ConversationTurn,
    PipelineInput,
    PipelineOutput,
    PolicyRef,
    Profile,
)

RENT = PolicyRef(policy_no="P-RENT", policy_name="청년 월세 지원")
MOVE = PolicyRef(policy_no="P-MOVE", policy_name="청년 이사비 지원")


class FakeLLM:
    chat_model = "fake-chat-model"
    embedding_model = "fake-embedding-model"
    analysis_model = "fake-analysis-model"

    def __init__(self, intent: Intent, refs: list[str], answer: str, region: str = "") -> None:
        self._sub = SubQuery(
            standalone_question="질문", search_query="청년 주거 지원", target_refs=refs, intent=intent,
            status_filter=StatusFilter.NONE, region=region, age=None, job_status=JobStatus.NONE, school=SchoolLevel.NONE,
            category=Category.NONE,
        )
        self._answer = answer
        self.chat_calls = 0
        self.stream_closed = False

    async def analyze(self, messages, text_format):
        return ParsedResult(QueryAnalysis(sub_queries=[self._sub], is_new_topic=False), 50, 10)

    async def chat_stream(self, messages):
        self.chat_calls += 1
        try:
            for start in range(0, len(self._answer), 3):
                yield self._answer[start:start + 3]
            yield ChatUsage(prompt_tokens=100, completion_tokens=20)
        finally:
            self.stream_closed = True


class FakeRetriever:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, query, policy_ids, k):
        self.calls += 1
        return [
            Chunk(chunk_id=uuid.uuid4(), policy_no=no, chunk_index=0, chunk_type="SUMMARY", content="내용", score=score)
            for no, score in (("P-RENT", 0.9), ("P-MOVE", 0.8))
        ]


class FakePolicyRepository:
    async def list_regions(self):
        return [RegionRow("11", "서울특별시", "강남구", "11680"), RegionRow("28", "인천광역시", "부평구", "28237")]

    async def get_by_ids(self, policy_nos):
        return {
            policy.policy_no: PolicyRecord(
                policy_no=policy.policy_no, policy_name=policy.policy_name, application_period_type_code=None,
                application_start_date=None, application_end_date=None, application_url=None,
                reference_url_1=None, reference_url_2=None, supervising_org_name=None,
            )
            for policy in (RENT, MOVE) if policy.policy_no in policy_nos
        }

    async def get_documents(self, policy_nos):
        return {no: [f"{no} 원문"] for no in policy_nos}

    async def get_region_labels(self, policy_nos):
        return {no: "서울특별시" for no in policy_nos}


class FakeCommonCodes:
    async def get_names(self, codes):
        return {}


def _deps(llm: FakeLLM) -> PipelineDeps:
    settings = Settings(openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test")
    return PipelineDeps(
        settings=settings, llm=llm, retriever=FakeRetriever(), policy_repository=FakePolicyRepository(),
        common_code_repository=FakeCommonCodes(),
    )


# 직전 답변에서 두 정책을 카드로 보여 준 상태
SHOWN = [ConversationTurn(question="주거 지원 알려줘", answer="…", policies=[RENT, MOVE])]


async def test_list_question_searches_and_llm_picks_cards():
    llm = FakeLLM(Intent.POLICY_LIST, [], "[소개: 2]\n1. 청년 이사비 지원")
    deps = _deps(llm)

    output = await run_pipeline(PipelineInput(question="주거 지원 알려줘"), deps)

    assert deps.retriever.calls == 1
    assert [policy.policy_no for policy in output.policies] == ["P-MOVE"]
    assert output.answer == "1. 청년 이사비 지원"
    assert (output.trace["handler"], output.trace["answer_kind"]) == ("search", "recommend")
    assert output.trace["prompt_version"] == "answer_common_v2+answer_cards_v2+answer_recommend_v1"
    assert output.trace["cards"] == {"candidate_nos": ["P-RENT", "P-MOVE"], "selected": [2], "fallback": False}
    assert output.trace["conditions"]["filtered_policy_count"] is None
    # 프로필이 없어 필터를 걸지 않음
    assert output.trace["filtered_policy_nos"] is None


async def test_all_candidates_shown_when_first_line_missing():
    output = await run_pipeline(
        PipelineInput(question="주거 지원 알려줘"), _deps(FakeLLM(Intent.POLICY_LIST, [], "첫 줄 없이 답변"))
    )

    assert [policy.policy_no for policy in output.policies] == ["P-RENT", "P-MOVE"]
    assert output.trace["cards"]["fallback"] is True


async def test_targeted_detail_uses_target_without_search():
    llm = FakeLLM(Intent.POLICY_DETAIL, ["2"], "청년 이사비 지원은 상시 신청할 수 있어요.")
    deps = _deps(llm)

    output = await run_pipeline(PipelineInput(question="두 번째 거 신청 방법은?", history=SHOWN), deps)

    assert deps.retriever.calls == 0
    assert [policy.policy_no for policy in output.policies] == ["P-MOVE"]
    assert output.policies[0].region_label == "서울특별시"
    assert output.answer == "청년 이사비 지원은 상시 신청할 수 있어요."
    assert (output.trace["handler"], output.trace["answer_kind"]) == ("detail", "detail")
    assert output.trace["prompt_version"] == "answer_common_v2+answer_detail_v1"
    assert output.current_policies == [MOVE]
    assert "filtered_policy_count" not in output.trace["conditions"]
    # 검색하지 않은 응답도 평가 서버가 읽는 키를 가짐
    assert (output.trace["retrieved_chunks"], output.trace["filtered_policy_nos"]) == ([], None)


async def test_chitchat_answers_without_evidence():
    llm = FakeLLM(Intent.CHITCHAT, [], "천만에요!")
    deps = _deps(llm)

    output = await run_pipeline(PipelineInput(question="고마워요", history=SHOWN), deps)

    assert deps.retriever.calls == 0
    assert output.policies == []
    assert output.trace["contexts"] == []
    assert output.trace["prompt_version"] == "answer_short_v1"
    assert (output.trace["retrieved_chunks"], output.trace["filtered_policy_nos"]) == ([], None)


async def test_missing_card_number_clarifies_without_llm_answer():
    llm = FakeLLM(Intent.POLICY_DETAIL, ["5"], "호출되면 안 됨")
    deps = _deps(llm)

    output = await run_pipeline(PipelineInput(question="다섯 번째 거 알려줘", history=SHOWN), deps)

    assert llm.chat_calls == 0
    assert deps.retriever.calls == 0
    assert output.answer.startswith("어느 정책을 말씀하시나요?\n1. 청년 월세 지원\n2. 청년 이사비 지원")
    assert [policy.policy_no for policy in output.policies] == ["P-RENT", "P-MOVE"]
    assert output.trace["handler"] == "clarify"
    assert output.trace["prompt_version"] is None
    assert output.trace["tokens"] == {"prompt": 0, "completion": 0}
    assert (output.trace["retrieved_chunks"], output.trace["filtered_policy_nos"]) == ([], None)
    assert set(output.trace["latency_ms"]) >= {"analyze", "generate", "total"}


SEOUL = Profile(age=27, region_code="11680")


async def test_question_region_different_from_profile_clarifies():
    llm = FakeLLM(Intent.POLICY_LIST, [], "호출되면 안 됨", region="인천")
    deps = _deps(llm)

    output = await run_pipeline(PipelineInput(question="인천 창업 지원 알려줘", profile=SEOUL, history=SHOWN), deps)

    assert llm.chat_calls == 0
    assert deps.retriever.calls == 0
    assert output.policies == []
    assert output.answer.startswith("질문에 말씀하신 지역(인천)이 프로필 지역(서울특별시 강남구)과 달라요.")
    assert (output.trace["handler"], output.trace["clarify_reason"]) == ("clarify", "condition")
    assert output.trace["conditions"]["mismatches"] == [
        {"name": "지역", "question": "인천", "profile": "서울특별시 강남구", "asks_back": True}
    ]


async def test_question_region_mismatch_on_targeted_policy_answers():
    llm = FakeLLM(Intent.ELIGIBILITY, ["2"], "인천 거주자는 조건이 달라요.", region="인천")
    deps = _deps(llm)

    output = await run_pipeline(
        PipelineInput(question="두 번째 거 인천 사람도 받을 수 있어?", profile=SEOUL, history=SHOWN), deps
    )

    assert llm.chat_calls == 1
    assert output.trace["handler"] == "detail"
    assert [policy.policy_no for policy in output.policies] == ["P-MOVE"]


# 채팅 SSE가 그대로 보낼 순서: 카드 → 본문 조각들 → 결과. 조각을 이으면 /internal 답변과 같음
async def test_stream_sends_cards_then_tokens_then_output():
    events = [
        event async for event in stream_pipeline(
            PipelineInput(question="주거 지원 알려줘"),
            _deps(FakeLLM(Intent.POLICY_LIST, [], "[소개: 2]\n1. 청년 이사비 지원")),
        )
    ]

    assert isinstance(events[0], PolicyCards)
    assert [policy.policy_no for policy in events[0].policies] == ["P-MOVE"]
    tokens = events[1:-1]
    assert tokens and all(isinstance(event, AnswerToken) for event in tokens)
    output = events[-1]
    assert isinstance(output, PipelineOutput)
    assert "".join(token.text for token in tokens) == output.answer == "1. 청년 이사비 지원"
    assert output.policies == events[0].policies
    latency = output.trace["latency_ms"]
    assert latency["analyze"] <= latency["first_token"] <= latency["total"]


# 채팅 연결이 끊겨 파이프라인 스트림이 닫히면 LLM 스트림도 바로 닫힘
async def test_closing_pipeline_stream_closes_llm_stream():
    llm = FakeLLM(Intent.POLICY_LIST, [], "[소개: 2]\n1. 청년 이사비 지원에 대한 긴 답변")
    events = stream_pipeline(PipelineInput(question="주거 지원 알려줘"), _deps(llm))

    assert isinstance(await anext(events), PolicyCards)
    assert isinstance(await anext(events), AnswerToken)
    await events.aclose()

    assert llm.stream_closed
