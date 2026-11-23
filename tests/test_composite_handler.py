import uuid
from datetime import date

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.pipeline.analyzer import AnalysisResult, ResolvedSubQuery
from app.pipeline.conditions import RegionIndex, build_conditions
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import CompareGroup
from app.pipeline.handlers import composite
from app.repositories.policy_repository import PolicyRecord, RegionRow
from app.schemas.analysis import Category, Intent, StatusFilter
from app.schemas.pipeline import Chunk, PipelineInput, PolicyRef, Profile

RENT = PolicyRef(policy_no="P-RENT", policy_name="청년 월세 지원")
MOVE = PolicyRef(policy_no="P-MOVE", policy_name="청년 이사비 지원")
LOAN = PolicyRef(policy_no="P-LOAN", policy_name="청년 전세대출")

NAMES = {
    "P-RENT": "청년 월세 지원",
    "P-RENT2": "서울 청년 월세 지원",
    "P-MOVE": "청년 이사비 지원",
    "P-LOAN": "청년 전세대출",
    "P-JOB1": "청년 취업 교육",
    "P-JOB2": "청년 직업훈련",
    "P-JOB3": "청년 인턴",
    "P-INCHEON": "인천 청년 취업 지원",
    "P-SAVE": "청년도약계좌",
}

# 검색어마다 결과가 다름. 월세 검색은 지목한 정책(P-RENT)도 돌려줌
RESULTS = {
    "월세 지원": [("P-RENT", 0.9), ("P-RENT2", 0.8), ("P-MOVE", 0.7)],
    "취업 교육": [("P-JOB1", 0.9), ("P-JOB2", 0.8), ("P-JOB3", 0.7), ("P-INCHEON", 0.6)],
    "청년도약계좌": [("P-SAVE", 0.9), ("P-JOB1", 0.5)],
    "없는 주제": [],
}
CHUNK_IDS = {no: uuid.uuid4() for no in NAMES}


def _record(policy_no: str) -> PolicyRecord:
    return PolicyRecord(
        policy_no=policy_no, policy_name=NAMES[policy_no], application_period_type_code=None,
        application_start_date=None, application_end_date=None, application_url=None,
        reference_url_1=None, reference_url_2=None, supervising_org_name=None,
    )


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []

    async def retrieve(self, query, policy_ids, k):
        self.calls.append((query, policy_ids))
        # 하위 질문끼리 같은 청크가 걸리는 경우를 흉내 내려고 정책마다 청크 id를 고정함
        return [
            Chunk(chunk_id=CHUNK_IDS[no], policy_no=no, chunk_index=0, chunk_type="SUMMARY", content="내용", score=score)
            for no, score in RESULTS.get(query, [])
        ]


class FakePolicyRepository:
    def __init__(self) -> None:
        self.filter_calls = 0

    async def filter_policy_nos(self, policy_filter, today):
        self.filter_calls += 1
        return list(NAMES)

    async def find_nos_by_name(self, name, policy_nos, limit):
        return [no for no, policy_name in NAMES.items() if policy_name == name]

    async def get_by_ids(self, policy_nos):
        return {no: _record(no) for no in policy_nos if no in NAMES}

    async def get_documents(self, policy_nos):
        return {no: [f"{no} 원문"] for no in policy_nos}

    async def get_region_labels(self, policy_nos):
        return {no: "인천광역시" if no == "P-INCHEON" else "전국" for no in policy_nos}


class FakeCommonCodes:
    async def get_names(self, codes):
        return {}


REGIONS = RegionIndex([RegionRow("11", "서울특별시", "강남구", "11680"), RegionRow("28", "인천광역시", "부평구", "28237")])


def _sub(
    question: str,
    intent: Intent = Intent.POLICY_LIST,
    targets: list[PolicyRef] | None = None,
    queries: list[str] | None = None,
    search_query: str | None = None,
    unresolved: list[str] | None = None,
) -> ResolvedSubQuery:
    return ResolvedSubQuery(
        standalone_question=question, target_refs=[], targets=targets or [], intent=intent,
        status_filter=StatusFilter.NONE, search_query=question if search_query is None else search_query,
        compare_queries=queries or [], unresolved_refs=unresolved or [], category=Category.NONE,
    )


def _ctx(*subs: ResolvedSubQuery, profile: Profile | None = None, candidates: int = 4) -> PipelineContext:
    analysis = AnalysisResult(
        sub_queries=list(subs), is_new_topic=False, fallback_reason=None, prompt_version="analyze_v10", model="m"
    )
    settings = Settings(
        openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test", card_candidates=candidates
    )
    deps = PipelineDeps(
        settings=settings, llm=None, retriever=FakeRetriever(), policy_repository=FakePolicyRepository(),
        common_code_repository=FakeCommonCodes(),
    )
    profile = profile or Profile(age=27)
    return PipelineContext(
        input=PipelineInput(question="질문", profile=profile), deps=deps, query="질문", today=date(2026, 10, 23),
        analysis=analysis, conditions=build_conditions(analysis, profile, REGIONS, {}),
    )


def _nos(evidence) -> list[str]:
    return [group.policy_no for group in evidence.candidates]


# 하위 질문마다 따로 검색하고, 후보 자리는 번갈아 고른 뒤 질문 순서대로 묶어 놓음
async def test_each_sub_question_searched_and_candidates_alternate():
    ctx = _ctx(_sub("월세 지원"), _sub("취업 교육"))

    evidence = await composite.collect(ctx)

    assert sorted(query for query, _ in ctx.deps.retriever.calls) == ["월세 지원", "취업 교육"]
    # 1차 필터는 한 번 계산해 두 검색에 같이 씀
    assert ctx.deps.policy_repository.filter_calls == 1
    assert all(policy_ids == list(NAMES) for _, policy_ids in ctx.deps.retriever.calls)
    assert _nos(evidence) == ["P-RENT", "P-RENT2", "P-JOB1", "P-JOB2"]
    assert evidence.composite_groups == [
        CompareGroup("질문 1: 월세 지원", 2, fixed=False), CompareGroup("질문 2: 취업 교육", 2, fixed=False)
    ]
    assert evidence.question_lines == ["1. 월세 지원", "2. 취업 교육"]
    assert evidence.fixed_cards is False
    assert evidence.filtered is True
    assert ctx.trace["composite"]["groups"][1] == {
        "name": "질문 2: 취업 교육", "fixed": False, "policy_nos": ["P-JOB1", "P-JOB2"]
    }
    # 평가 서버가 읽는 키는 한 목록
    assert len(ctx.trace["retrieved_chunks"]) == 7


async def test_empty_search_leaves_slots_to_other_question():
    evidence = await composite.collect(_ctx(_sub("없는 주제"), _sub("취업 교육")))

    assert _nos(evidence) == ["P-JOB1", "P-JOB2", "P-JOB3", "P-INCHEON"]
    assert [group.size for group in evidence.composite_groups] == [0, 4]


# 지목한 정책은 검색 없이 고정하고, 같은 정책은 다른 묶음에 다시 넣지 않음
async def test_targeted_sub_question_fixed_and_not_repeated():
    ctx = _ctx(_sub("월세 지원 신청 기간", Intent.POLICY_DETAIL, targets=[RENT]), _sub("월세 지원"))

    evidence = await composite.collect(ctx)

    assert [query for query, _ in ctx.deps.retriever.calls] == ["월세 지원"]
    assert _nos(evidence) == ["P-RENT", "P-RENT2", "P-MOVE"]
    assert evidence.composite_groups[0] == CompareGroup("질문 1: 월세 지원 신청 기간", 1, fixed=True)
    assert evidence.fixed_cards is False


# 답하지 않을 하위 질문은 근거 없이 이유만 표시하고, 인사는 목록에서 뺌
async def test_skipped_sub_questions_marked_with_reason():
    ctx = _ctx(
        _sub("안녕하세요", Intent.CHITCHAT),
        _sub("중위소득이 뭐야", Intent.OUT_OF_SCOPE, search_query=""),
        _sub("월세 지원"),
        _sub("그거 신청 기간", Intent.UNCLEAR),
    )

    evidence = await composite.collect(ctx)

    assert [query for query, _ in ctx.deps.retriever.calls] == ["월세 지원"]
    assert evidence.question_lines == [
        "1. 중위소득이 뭐야 (따로 답하지 않음: 청년 정책 질문이 아님)",
        "2. 월세 지원",
        "3. 그거 신청 기간 (따로 답하지 않음: 어느 정책인지 알 수 없음)",
    ]
    assert [group.name for group in evidence.composite_groups] == ["질문 2: 월세 지원"]


# 비교는 대상별 카드 규칙이 따로라 복합 안에서는 답하지 않고 단일 질문으로 물어 달라고 함
async def test_compare_sub_question_is_not_answered():
    ctx = _ctx(_sub("월세 지원이랑 도약계좌 비교", Intent.COMPARISON, targets=[RENT], queries=["청년도약계좌"]),
               _sub("취업 교육"))

    evidence = await composite.collect(ctx)

    assert evidence.question_lines[0] == "1. 월세 지원이랑 도약계좌 비교 (따로 답하지 않음: 비교는 따로)"
    assert [query for query, _ in ctx.deps.retriever.calls] == ["취업 교육"]
    assert "P-RENT" not in _nos(evidence)


# 카드 자리(3개)를 넘는 하위 질문(지목을 여럿 한 경우)은 안내로 돌림
async def test_sub_question_over_card_limit_is_not_answered():
    ctx = _ctx(
        _sub("두 정책 신청 기간", Intent.POLICY_DETAIL, targets=[RENT, MOVE]),
        _sub("전세대출 대상", Intent.ELIGIBILITY, targets=[LOAN]),
        _sub("취업 교육"),
    )

    evidence = await composite.collect(ctx)

    assert evidence.question_lines[2] == "3. 취업 교육 (따로 답하지 않음: 한 번에 보여 줄 정책 수를 넘음)"
    assert ctx.deps.retriever.calls == []
    assert _nos(evidence) == ["P-RENT", "P-MOVE", "P-LOAN"]


# 검색하지 않으면 1차 필터를 계산하지 않고, 고를 묶음이 없어 카드를 미리 정함
async def test_targets_without_search_fix_cards():
    ctx = _ctx(_sub("월세 신청 기간", Intent.POLICY_DETAIL, targets=[RENT]), _sub("날씨", Intent.OUT_OF_SCOPE))

    evidence = await composite.collect(ctx)

    assert ctx.deps.policy_repository.filter_calls == 0
    assert ctx.deps.retriever.calls == []
    assert evidence.fixed_cards is True
    assert evidence.filtered is False
    assert "retrieved_chunks" not in ctx.trace


# 지역 정책 먼저는 묶음 안에서만 (묶음 순서는 질문 순서 그대로)
async def test_local_policies_first_within_group():
    ctx = _ctx(_sub("월세 지원"), _sub("취업 교육"), profile=Profile(region_code="28237"), candidates=7)

    evidence = await composite.collect(ctx)

    assert _nos(evidence) == ["P-RENT", "P-RENT2", "P-MOVE", "P-INCHEON", "P-JOB1", "P-JOB2", "P-JOB3"]
