import uuid
from datetime import date

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.pipeline.analyzer import AnalysisResult, ResolvedSubQuery
from app.pipeline.conditions import RegionIndex, build_conditions
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import AnswerKind, CompareGroup
from app.pipeline.handlers import compare
from app.repositories.policy_repository import PolicyRecord, RegionRow
from app.schemas.analysis import Intent, StatusFilter
from app.schemas.pipeline import Chunk, PipelineInput, PolicyRef, Profile
from app.schemas.policy_info import CLOSED_CODE

RENT = PolicyRef(policy_no="P-RENT", policy_name="청년 월세 지원")
MOVE = PolicyRef(policy_no="P-MOVE", policy_name="청년 이사비 지원")
LOAN = PolicyRef(policy_no="P-LOAN", policy_name="청년 전세대출")

NAMES = {
    "P-RENT": "청년 월세 지원",
    "P-MOVE": "청년 이사비 지원",
    "P-LOAN": "청년 전세대출",
    "P-SAVE": "청년 자산형성 지원(청년도약계좌)",
    "P-JOB": "청년일자리도약장려금",
    "P-CAMP": "청년도약 인재양성 부트캠프",
    "P-OLD": "청년 자산형성 지원(청년도약계좌)",
    "P-DEPOSIT": "청년내일저축계좌",
    "P-DEPOSIT2": "청년내일저축계좌 운영",
    "P-CLOSED": "청년 저축 마감",
}


def _record(policy_no: str, code: str | None = None) -> PolicyRecord:
    return PolicyRecord(
        policy_no=policy_no,
        policy_name=NAMES[policy_no],
        application_period_type_code=code,
        application_start_date=None,
        application_end_date=None,
        application_url=None,
        reference_url_1=None,
        reference_url_2=None,
        supervising_org_name=None,
    )


RECORDS = {no: _record(no, CLOSED_CODE if no == "P-CLOSED" else None) for no in NAMES}


def _chunk(policy_no: str, score: float) -> Chunk:
    return Chunk(
        chunk_id=uuid.uuid4(), policy_no=policy_no, chunk_index=0, chunk_type="SUMMARY", content="내용", score=score
    )


# "도약계좌"처럼 줄인 이름은 벡터가 정식 이름 정책을 놓치는 경우를 흉내 냄
VECTOR_RESULTS = {
    "도약계좌": [("P-JOB", 0.8), ("P-CAMP", 0.7), ("P-DEPOSIT", 0.6), ("P-CLOSED", 0.59)],
    "내일저축계좌": [("P-DEPOSIT", 0.9), ("P-SAVE", 0.8), ("P-DEPOSIT2", 0.7), ("P-JOB", 0.6), ("P-OLD", 0.5)],
}
NAME_HITS = {"도약계좌": ["P-SAVE", "P-OLD"], "내일저축계좌": ["P-DEPOSIT", "P-DEPOSIT2"]}


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []

    async def retrieve(self, query, policy_ids, k):
        self.calls.append((query, policy_ids))
        return [_chunk(no, score) for no, score in VECTOR_RESULTS.get(query, [])]


class FakePolicyRepository:
    def __init__(self, filtered: list[str] | None = None) -> None:
        self.filtered = filtered
        self.name_calls: list[tuple[str, list[str] | None]] = []

    async def filter_policy_nos(self, policy_filter, today):
        return self.filtered

    async def find_nos_by_name(self, name, policy_nos, limit):
        self.name_calls.append((name, policy_nos))
        return NAME_HITS.get(name, [])

    async def get_by_ids(self, policy_nos):
        return {no: RECORDS[no] for no in policy_nos if no in RECORDS}

    async def get_documents(self, policy_nos):
        return {no: [f"{no} 원문"] for no in policy_nos}

    async def get_region_labels(self, policy_nos):
        return {no: "전국" for no in policy_nos}


class FakeCommonCodes:
    async def get_names(self, codes):
        return {}


REGIONS = RegionIndex([RegionRow("11", "서울특별시", "강남구", "11680")])


def _ctx(
    targets: list[PolicyRef],
    queries: list[str],
    repository: FakePolicyRepository | None = None,
    answer_kind: AnswerKind = AnswerKind.COMPARE,
    profile: Profile | None = None,
) -> PipelineContext:
    sub = ResolvedSubQuery(
        standalone_question="비교", target_refs=[], targets=targets, intent=Intent.COMPARISON,
        status_filter=StatusFilter.NONE, compare_queries=queries,
    )
    analysis = AnalysisResult(
        sub_queries=[sub], is_new_topic=False, fallback_reason=None, prompt_version="analyze_v8", model="m"
    )
    settings = Settings(openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test")
    deps = PipelineDeps(
        settings=settings,
        llm=None,
        retriever=FakeRetriever(),
        policy_repository=repository or FakePolicyRepository(),
        common_code_repository=FakeCommonCodes(),
    )
    profile = profile or Profile()
    return PipelineContext(
        input=PipelineInput(question="비교해 줘", profile=profile),
        deps=deps,
        query="비교해 줘",
        today=date(2026, 10, 23),
        analysis=analysis,
        answer_kind=answer_kind,
        conditions=build_conditions(analysis, profile, REGIONS, {}),
    )


async def test_targets_only_fixed_cards_without_search():
    ctx = _ctx([RENT, MOVE], [])

    evidence = await compare.collect(ctx)

    assert [group.policy_no for group in evidence.candidates] == ["P-RENT", "P-MOVE"]
    assert evidence.compare_groups == [
        CompareGroup("청년 월세 지원", 1, fixed=True), CompareGroup("청년 이사비 지원", 1, fixed=True)
    ]
    assert evidence.fixed_cards is True
    assert evidence.filtered is False
    assert ctx.deps.retriever.calls == []
    assert "retrieved_chunks" not in ctx.trace


# 이름 일치가 먼저 오고(벡터가 놓친 정식 이름 포함), 같은 이름은 하나만, 남는 자리는 벡터 순위
async def test_name_matches_first_then_vector():
    ctx = _ctx([], ["도약계좌", "내일저축계좌"])

    evidence = await compare.collect(ctx)

    nos = [group.policy_no for group in evidence.candidates]
    assert nos[:3] == ["P-SAVE", "P-JOB", "P-CAMP"]
    assert "P-OLD" not in nos
    assert evidence.compare_groups == [
        CompareGroup("도약계좌", 3, fixed=False), CompareGroup("내일저축계좌", 2, fixed=False)
    ]
    assert evidence.fixed_cards is False
    assert evidence.filtered is True


# 앞 대상의 후보는 뒤 대상에서 빼서 같은 정책끼리 비교하지 않음. 번호만 다른 같은 이름(P-OLD)도 뺌
# 남는 후보가 적으면 그만큼만 둠
async def test_later_target_skips_policies_already_used():
    ctx = _ctx([], ["도약계좌", "내일저축계좌"])

    evidence = await compare.collect(ctx)

    assert [group.policy_no for group in evidence.candidates[3:]] == ["P-DEPOSIT", "P-DEPOSIT2"]


async def test_target_and_query_order_and_trace():
    ctx = _ctx([RENT], ["도약계좌"])

    evidence = await compare.collect(ctx)

    assert [group.policy_no for group in evidence.candidates] == ["P-RENT", "P-SAVE", "P-JOB", "P-CAMP"]
    assert ctx.trace["compare"] == {
        "target_policy_nos": ["P-RENT"],
        "queries": [{"query": "도약계좌", "candidate_nos": ["P-SAVE", "P-JOB", "P-CAMP"], "name_match_count": 2}],
    }
    assert {chunk["policy_no"] for chunk in ctx.trace["retrieved_chunks"]} == {"P-JOB", "P-CAMP", "P-DEPOSIT", "P-CLOSED"}
    assert set(evidence.policies) == {"P-RENT", "P-SAVE", "P-JOB", "P-CAMP"}


# 프로필 조건이 있으면 벡터 검색과 이름 일치 모두 1차 필터 안에서 찾음
async def test_profile_filter_applies_to_both_searches():
    repository = FakePolicyRepository(filtered=["P-SAVE", "P-JOB"])
    ctx = _ctx([], ["도약계좌", "내일저축계좌"], repository, profile=Profile(age=27))

    await compare.collect(ctx)

    assert ctx.deps.retriever.calls == [("도약계좌", ["P-SAVE", "P-JOB"]), ("내일저축계좌", ["P-SAVE", "P-JOB"])]
    assert repository.name_calls == [("도약계좌", ["P-SAVE", "P-JOB"]), ("내일저축계좌", ["P-SAVE", "P-JOB"])]
    assert ctx.trace["filtered_policy_nos"] == ["P-SAVE", "P-JOB"]


async def test_query_without_candidates_keeps_empty_group():
    ctx = _ctx([RENT], ["없는 정책"])

    evidence = await compare.collect(ctx)

    assert [group.policy_no for group in evidence.candidates] == ["P-RENT"]
    assert evidence.compare_groups[1] == CompareGroup("없는 정책", 0, fixed=False)


async def test_too_many_targets_offers_options():
    ctx = _ctx([RENT, MOVE, LOAN], [], answer_kind=AnswerKind.CLARIFY)

    evidence = await compare.collect(ctx)

    assert evidence.reply == "\n".join([
        "비교는 두 정책씩 할 수 있어요. 비교할 두 정책을 골라 주세요.",
        "1. 청년 월세 지원",
        "2. 청년 이사비 지원",
        "3. 청년 전세대출",
        "예: '1번이랑 2번 비교해 줘'",
    ])
    assert [group.policy_no for group in evidence.candidates] == ["P-RENT", "P-MOVE", "P-LOAN"]
    assert ctx.trace["clarify_reason"] == "compare_limit"
    assert ctx.deps.retriever.calls == []


async def test_too_many_with_names_asks_without_cards():
    ctx = _ctx([RENT], ["도약계좌", "내일저축계좌"], answer_kind=AnswerKind.CLARIFY)

    evidence = await compare.collect(ctx)

    assert evidence.reply == "\n".join([
        "비교는 두 정책씩 할 수 있어요.",
        "말씀하신 정책: 청년 월세 지원, 도약계좌, 내일저축계좌",
        "이 중 두 개를 골라 '청년 월세 지원, 도약계좌 비교해 줘'처럼 다시 물어봐 주세요.",
    ])
    assert evidence.candidates == []
    assert ctx.deps.retriever.calls == []


# 뒤 대상의 이름과 일치한 정책(P-DEPOSIT)이 앞 대상의 벡터 결과에 먼저 걸려도 뒤 대상에 남김
async def test_name_match_of_later_target_stays_there():
    ctx = _ctx([], ["도약계좌", "내일저축계좌"])
    VECTOR_RESULTS["도약계좌"].insert(0, ("P-DEPOSIT", 0.95))
    try:
        evidence = await compare.collect(ctx)
    finally:
        VECTOR_RESULTS["도약계좌"].pop(0)

    nos = [group.policy_no for group in evidence.candidates]
    assert nos[:3] == ["P-SAVE", "P-JOB", "P-CAMP"]
    assert nos[3:] == ["P-DEPOSIT", "P-DEPOSIT2"]
