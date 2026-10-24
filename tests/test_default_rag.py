import uuid
from datetime import date

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.llm.client import ChatResult
from app.pipeline.analyzer import AnalysisResult, ResolvedSubQuery
from app.pipeline.context import PipelineContext
from app.pipeline.handlers import default_rag
from app.repositories.policy_repository import PolicyFilter, PolicyRecord, RegionRow
from app.schemas.analysis import Intent, StatusFilter
from app.schemas.pipeline import Chunk, PipelineInput, Profile
from app.schemas.policy_info import CLOSED_CODE


def _chunk(policy_no: str, score: float) -> Chunk:
    return Chunk(
        chunk_id=uuid.uuid4(), policy_no=policy_no, chunk_index=0, chunk_type="SUMMARY", content="내용", score=score
    )


def _record(policy_no: str, code: str | None = None) -> PolicyRecord:
    return PolicyRecord(
        policy_no=policy_no,
        policy_name=f"정책 {policy_no}",
        application_period_type_code=code,
        application_start_date=None,
        application_end_date=None,
        application_url=None,
        reference_url_1=None,
        reference_url_2=None,
        supervising_org_name=None,
    )


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []

    async def retrieve(self, query, policy_ids, k):
        self.calls.append((query, policy_ids))
        # 확실한 1위는 상태와 관계없이 남으므로 마감 정책을 2위로 둠
        return [_chunk("P-A", 0.9), _chunk("P-CLOSED", 0.8), _chunk("P-B", 0.7), _chunk("P-C", 0.6)]


class FakePolicyRepository:
    def __init__(self) -> None:
        self.document_requests: list[list[str]] = []
        self.filters: list[PolicyFilter] = []

    async def list_regions(self):
        return [RegionRow("11", "서울특별시", "강남구"), RegionRow("28", "인천광역시", "부평구")]

    async def filter_policy_nos(self, policy_filter, today):
        self.filters.append(policy_filter)
        return ["P-A", "P-B", "P-C", "P-CLOSED"]

    async def nationwide_policy_nos(self):
        return frozenset({"P-B", "P-C"})

    async def get_region_labels(self, policy_nos):
        return {no: "전국" for no in policy_nos}

    async def get_by_ids(self, policy_nos):
        records = [_record("P-CLOSED", CLOSED_CODE), _record("P-A"), _record("P-B"), _record("P-C")]
        return {record.policy_no: record for record in records if record.policy_no in policy_nos}

    async def get_documents(self, policy_nos):
        self.document_requests.append(policy_nos)
        return {no: [f"{no} 원문"] for no in policy_nos}


class FakeCommonCodes:
    async def get_names(self, codes):
        return {}


class FakeLLM:
    chat_model = "fake-chat-model"
    embedding_model = "fake-embedding-model"

    def __init__(self, text: str) -> None:
        self._text = text

    async def chat(self, messages):
        return ChatResult(text=self._text, prompt_tokens=100, completion_tokens=20)


def _ctx(
    llm_text: str,
    repository: FakePolicyRepository,
    analysis: AnalysisResult | None = None,
    profile: Profile | None = None,
) -> PipelineContext:
    settings = Settings(
        openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test", card_candidates=3
    )
    deps = PipelineDeps(
        settings=settings,
        llm=FakeLLM(llm_text),
        retriever=FakeRetriever(),
        policy_repository=repository,
        common_code_repository=FakeCommonCodes(),
    )
    question = "주거 지원 알려줘"
    return PipelineContext(
        input=PipelineInput(question=question, profile=profile or Profile()),
        deps=deps,
        query=question,
        search_query="청년 주거 지원",
        today=date(2026, 10, 23),
        analysis=analysis,
    )


def _analysis(**sub_fields) -> AnalysisResult:
    sub = ResolvedSubQuery(
        standalone_question="q", target_refs=[], targets=[], intent=Intent.POLICY_LIST,
        status_filter=sub_fields.pop("status_filter", StatusFilter.NONE), **sub_fields,
    )
    return AnalysisResult(
        sub_queries=[sub], is_new_topic=False, fallback_reason=None, prompt_version="analyze_v3", model="m"
    )


async def test_cards_follow_status_order_and_selection():
    repository = FakePolicyRepository()

    output = await default_rag.handle(_ctx("[소개: 1,3]\n1. 정책 P-A\n2. 정책 P-C", repository))

    # 마감 정책은 점수가 더 높아도 뒤로 밀려 후보 3개에서 빠짐
    assert repository.document_requests == [["P-A", "P-B", "P-C"]]
    assert [policy.policy_no for policy in output.policies] == ["P-A", "P-C"]
    assert output.answer == "1. 정책 P-A\n2. 정책 P-C"
    assert output.trace["cards"] == {"candidate_nos": ["P-A", "P-B", "P-C"], "selected": [1, 3], "fallback": False}
    assert output.trace["prompt_version"] == "generate_v4"
    assert output.policies[0].region_label == "전국"


async def test_no_cards_when_nothing_selected():
    output = await default_rag.handle(_ctx("[소개: ]\n관련 정책을 찾지 못했어요.", FakePolicyRepository()))

    assert output.policies == []
    assert output.answer == "관련 정책을 찾지 못했어요."


async def test_all_candidates_shown_when_first_line_missing():
    output = await default_rag.handle(_ctx("첫 줄 없이 답변", FakePolicyRepository()))

    assert [policy.policy_no for policy in output.policies] == ["P-A", "P-B", "P-C"]
    assert output.answer == "첫 줄 없이 답변"
    assert output.trace["cards"]["fallback"] is True


async def test_question_region_filters_and_search_query_used():
    repository = FakePolicyRepository()
    ctx = _ctx(
        "[소개: 1]\n답변",
        repository,
        analysis=_analysis(region="인천", status_filter=StatusFilter.OPEN_ONLY),
        profile=Profile(age=27, region_code="11680"),
    )

    output = await default_rag.handle(ctx)

    # 질문 지역이 프로필 지역보다 우선하고, 나이는 프로필 값을 씀
    assert repository.filters == [
        PolicyFilter(sido_code="28", age=27, exclude_closed=True, exclude_upcoming=True)
    ]
    # 지역 조건이 있으면 지역 정책과 전국 정책을 같은 검색어로 따로 검색함
    assert ctx.deps.retriever.calls == [("청년 주거 지원", ["P-A", "P-CLOSED"]), ("청년 주거 지원", ["P-B", "P-C"])]
    assert output.trace["conditions"] == {
        "lines": ["지역: 인천광역시 (질문)", "나이: 27세 (프로필)", "신청 상태: 지금 신청 가능한 정책만 (질문)"],
        "filtered_policy_count": 4,
        "split_search": True,
    }


async def test_no_filter_when_analysis_missing():
    repository = FakePolicyRepository()
    ctx = _ctx("[소개: 1]\n답변", repository, profile=Profile(age=27, region_code="11680"))

    output = await default_rag.handle(ctx)

    assert repository.filters == []
    assert ctx.deps.retriever.calls == [("청년 주거 지원", None)]
    assert output.trace["conditions"] == {"lines": [], "filtered_policy_count": None, "split_search": False}
