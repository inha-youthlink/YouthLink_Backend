import uuid
from datetime import date

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.pipeline.analyzer import AnalysisResult, ResolvedSubQuery
from app.pipeline.conditions import RegionIndex, build_conditions
from app.pipeline.context import PipelineContext
from app.pipeline.handlers import search
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
        # 마감 정책은 2위지만 다른 정책(정보 없음)과의 점수 차가 감점 차이보다 작음
        return [_chunk("P-A", 0.9), _chunk("P-CLOSED", 0.71), _chunk("P-B", 0.7), _chunk("P-C", 0.69)]


REGIONS = RegionIndex([RegionRow("11", "서울특별시", "강남구"), RegionRow("28", "인천광역시", "부평구")])


class FakePolicyRepository:
    def __init__(self) -> None:
        self.document_requests: list[list[str]] = []
        self.filters: list[PolicyFilter] = []

    async def filter_policy_nos(self, policy_filter, today):
        self.filters.append(policy_filter)
        return ["P-A", "P-B", "P-C", "P-CLOSED"]

    async def get_region_labels(self, policy_nos):
        return {no: "인천광역시 부평구" if no == "P-C" else "전국" for no in policy_nos}

    async def get_by_ids(self, policy_nos):
        records = [_record("P-CLOSED", CLOSED_CODE), _record("P-A"), _record("P-B"), _record("P-C")]
        return {record.policy_no: record for record in records if record.policy_no in policy_nos}

    async def get_documents(self, policy_nos):
        self.document_requests.append(policy_nos)
        return {no: [f"{no} 원문"] for no in policy_nos}


class FakeCommonCodes:
    async def get_names(self, codes):
        return {}


def _ctx(
    repository: FakePolicyRepository,
    analysis: AnalysisResult | None = None,
    profile: Profile | None = None,
) -> PipelineContext:
    settings = Settings(
        openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test", card_candidates=3
    )
    deps = PipelineDeps(
        settings=settings,
        llm=None,
        retriever=FakeRetriever(),
        policy_repository=repository,
        common_code_repository=FakeCommonCodes(),
    )
    question = "주거 지원 알려줘"
    profile = profile or Profile()
    # 조건은 runner가 핸들러 전에 계산함
    return PipelineContext(
        input=PipelineInput(question=question, profile=profile),
        deps=deps,
        query=question,
        search_query="청년 주거 지원",
        today=date(2026, 10, 23),
        analysis=analysis,
        conditions=build_conditions(analysis, profile, REGIONS, {}),
    )


def _analysis(**sub_fields) -> AnalysisResult:
    sub = ResolvedSubQuery(
        standalone_question="q", target_refs=[], targets=[], intent=Intent.POLICY_LIST,
        status_filter=sub_fields.pop("status_filter", StatusFilter.NONE), **sub_fields,
    )
    return AnalysisResult(
        sub_queries=[sub], is_new_topic=False, fallback_reason=None, prompt_version="analyze_v3", model="m"
    )


async def test_candidates_use_status_penalty():
    repository = FakePolicyRepository()

    evidence = await search.collect(_ctx(repository))

    # 마감 정책은 감점 뒤 점수가 3위보다 낮아져 후보 3개에서 빠짐
    assert [group.policy_no for group in evidence.candidates] == ["P-A", "P-B", "P-C"]
    assert repository.document_requests == [["P-A", "P-B", "P-C"]]
    assert evidence.documents["P-A"] == ["P-A 원문"]
    assert evidence.region_labels["P-A"] == "전국"
    # 카드는 LLM이 첫 줄로 고름
    assert evidence.fixed_cards is False


async def test_profile_conditions_filter_and_search_query_used():
    repository = FakePolicyRepository()
    ctx = _ctx(
        repository,
        analysis=_analysis(status_filter=StatusFilter.OPEN_ONLY),
        profile=Profile(age=27, region_code="28237"),
    )

    evidence = await search.collect(ctx)

    assert repository.filters == [
        PolicyFilter(sido_code="28", age=27, exclude_closed=True, exclude_upcoming=True)
    ]
    assert ctx.deps.retriever.calls == [("청년 주거 지원", ["P-A", "P-B", "P-C", "P-CLOSED"])]
    # 프로필 지역(인천) 정책이 카드 앞으로 옴
    assert [group.policy_no for group in evidence.candidates] == ["P-C", "P-A", "P-B"]
    assert evidence.filtered is True
    assert ctx.trace["conditions"] == {"filtered_policy_count": 4}


async def test_no_filter_without_profile():
    repository = FakePolicyRepository()
    ctx = _ctx(repository)

    await search.collect(ctx)

    assert repository.filters == []
    assert ctx.deps.retriever.calls == [("청년 주거 지원", None)]
    assert ctx.trace["conditions"] == {"filtered_policy_count": None}
