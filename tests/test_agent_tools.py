from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from app.core.dependencies import PipelineDeps
from app.llm.client import ToolCall
from app.pipeline.agent.tools import AgentTools, SeenPolicies, progress_text
from app.pipeline.conditions import Condition, Region, SearchConditions
from app.repositories.policy_repository import PolicyBriefRecord, PolicyDetailRecord, PolicyRecord
from app.schemas.pipeline import Chunk
from app.schemas.policy_info import ALWAYS_OPEN_CODE

TODAY = date(2026, 11, 22)
CONDITIONS = SearchConditions(
    region=Region("11", "서울특별시", "서울특별시 강남구", ("11680",)),
    age=Condition(27, "27세"),
    job=Condition("0013003", "미취업자"),
)


def _record(no: str, name: str, large: list[str], **fields) -> PolicyRecord:
    values = {
        "application_period_type_code": "0057001", "application_start_date": date(2026, 11, 1),
        "application_end_date": date(2026, 12, 31), "application_url": None, "reference_url_1": None,
        "reference_url_2": None, "supervising_org_name": "서울특별시", **fields,
    }
    return PolicyRecord(policy_no=no, policy_name=name, large_categories=large, **values)


BRIEFS = {
    "R1": PolicyBriefRecord(_record("R1", "청년 월세 지원", ["주거"]), ["전월세 및 주거급여 지원"],
                            "무주택 청년에게 월세를 매달 지원하는 사업입니다", "월 20만 원"),
    # 같은 이름의 다른 공고 (점수가 낮아 빠짐)
    "R2": PolicyBriefRecord(_record("R2", "청년 월세  지원", ["주거"]), ["전월세 및 주거급여 지원"], "짧음", "월세 지원금"),
    "H1": PolicyBriefRecord(
        _record("H1", "청년 임대주택", ["주거"], application_period_type_code=ALWAYS_OPEN_CODE),
        ["주택 및 거주지"], "청년 임대주택", "❍ 시세의 ㆍ 80% 수준으로\n 임대주택 공급",
    ),
}
CHUNK_SCORES = [("R1", 0.9), ("R2", 0.8), ("H1", 0.7), ("R1", 0.6)]


class FakePolicies:
    def __init__(self) -> None:
        self.filters: list = []

    async def filter_policy_nos(self, policy_filter, today):
        self.filters.append(policy_filter)
        if policy_filter.middle_categories:
            return [no for no, brief in BRIEFS.items()
                    if set(brief.middle_categories) & set(policy_filter.middle_categories)]
        return list(BRIEFS)

    async def count_middle_categories(self, policy_nos, large):
        return {"전월세 및 주거급여 지원": 2, "주택 및 거주지": 1}

    async def get_briefs(self, policy_nos):
        return {no: BRIEFS[no] for no in policy_nos if no in BRIEFS}

    async def get_region_labels(self, policy_nos):
        return {no: ("전국" if no == "H1" else "서울특별시") for no in policy_nos}

    async def get_detail(self, policy_no):
        brief = BRIEFS[policy_no]
        return PolicyDetailRecord(
            record=brief.record, description=brief.description, support_content="월 최대 20만 원, 12개월",
            application_method="온라인 신청", submission_documents=None, additional_qualification="무주택자",
            participation_exclusion=None, operating_org_name=None, min_age=19, max_age=39,
            income_condition_code=None, min_income=None, max_income=None, income_etc=None, marriage_name=None,
        )


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list = []

    async def retrieve(self, query, policy_ids, k):
        self.calls.append((query, policy_ids, k))
        return [
            Chunk(chunk_id=uuid4(), policy_no=no, chunk_index=i, chunk_type="t", content="c", score=score)
            for i, (no, score) in enumerate(CHUNK_SCORES) if no in policy_ids
        ]


def _tools(seen: SeenPolicies | None = None):
    policies, retriever = FakePolicies(), FakeRetriever()
    deps = PipelineDeps(SimpleNamespace(top_k=30), None, retriever, policies, None)
    return AgentTools(deps, CONDITIONS, seen or SeenPolicies(), TODAY), policies, retriever


def _search(**arguments):
    return {"query": "월세", "category": "주거", "sub_categories": [], "include_closed": False,
            "local_only": False, **arguments}


# 인자에는 프로필 조건이 없고 서버가 항상 검. 마감은 원할 때만 포함
async def test_search_always_applies_profile_filter():
    tools, policies, retriever = _tools()
    await tools.execute("search_policies", _search())
    await tools.execute("search_policies", _search(include_closed=True, category=None))
    first, second = policies.filters
    assert first == replace(CONDITIONS.to_filter(), exclude_closed=True, large_categories=("주거",))
    assert second == CONDITIONS.to_filter()
    assert retriever.calls[0] == ("월세", ["R1", "R2", "H1"], 30)


async def test_search_result_lines():
    tools, _, _ = _tools()
    output = await tools.execute("search_policies", _search())
    assert output.splitlines()[:2] == [
        "조건에 맞는 정책 3개 중 관련도 순 2개 (이름이 같은 정책은 하나만)",
        "세부 분류: 전월세 및 주거급여 지원 2 / 주택 및 거주지 1",
    ]
    assert "P1 | 청년 월세 지원 | 주거 / 전월세 및 주거급여 지원 | 신청 가능, 2026-12-31 마감 | 서울특별시 | 무주택 청년에게" in output
    # 설명이 이름과 같으면 지원 내용을 기호 없이 씀. 상시는 마감일 없음
    assert "P2 | 청년 임대주택 | 주거 / 주택 및 거주지 | 상시 모집 | 전국 | 시세의 80% 수준으로 임대주택 공급" in output
    assert tools.records[0].policy_nos == ["R1", "H1"]
    assert tools.records[0].filtered_policy_count == 3


async def test_search_sub_categories_and_unknown_names():
    tools, policies, _ = _tools()
    output = await tools.execute("search_policies", _search(sub_categories=["주택 및 거주지", "공공임대·주택"]))
    assert policies.filters[1].middle_categories == ("주택 및 거주지",)
    assert "세부 분류 이름이 아니라 빼고 찾음: 공공임대·주택" in output
    assert tools.records[0].policy_nos == ["H1"]


# 선택지 글자처럼 띄어쓰기·"·"·"및"만 다른 이름은 실제 이름으로 맞춤
async def test_search_sub_categories_loose_names():
    tools, policies, _ = _tools()
    output = await tools.execute("search_policies", _search(sub_categories=["주택·거주지", "전월세 및 주거급여지원"]))
    assert policies.filters[1].middle_categories == ("전월세 및 주거급여 지원", "주택 및 거주지")
    assert "빼고 찾음" not in output


async def test_search_local_only_drops_nationwide():
    tools, _, _ = _tools()
    await tools.execute("search_policies", _search(local_only=True))
    assert tools.records[0].policy_nos == ["R1"]


# 번호는 대화 전체에서 이어짐: 이전 턴에 본 정책은 같은 번호
async def test_labels_continue_from_seen():
    tools, _, _ = _tools(SeenPolicies(["H1"]))
    output = await tools.execute("search_policies", _search())
    assert "P2 | 청년 월세 지원" in output
    assert "P1 | 청년 임대주택" in output


async def test_get_policies_only_seen():
    seen = SeenPolicies(["R1"])
    tools, _, _ = _tools(seen)
    output = await tools.execute("get_policies", {"policies": ["P1", "P9", "X", "p1"]})
    assert output.startswith("P1 | 청년 월세 지원 | 서울특별시")
    assert "- 지원 내용: 월 최대 20만 원, 12개월" in output
    assert "- 신청 기간: 2026-11-01 ~ 2026-12-31" in output
    assert "P9: 이번 대화에서 본 정책이 아님" in output
    assert tools.records[0].policy_nos == ["R1"]


async def test_unknown_tool():
    tools, _, _ = _tools()
    assert await tools.execute("delete_everything", {}) == "없는 도구: delete_everything"
    assert tools.records[0].error == "unknown_tool"


def test_seen_policies():
    seen = SeenPolicies(["A", "B"])
    assert seen.label("B") == "P2"
    assert seen.label("C") == "P3"
    assert [seen.resolve(label) for label in ("P1", " p3 ", "P0", "P4", "3")] == ["A", "C", None, None, None]


def test_progress_text():
    assert progress_text(ToolCall("search_policies", {"category": "주거"})) == "주거 정책 찾는 중…"
    assert progress_text(ToolCall("search_policies", {"category": None})) == "정책 찾는 중…"
    assert progress_text(ToolCall("get_policies", {"policies": ["P1"]})) == "정책 내용 확인 중…"
