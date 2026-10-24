from datetime import date

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.pipeline.analyzer import AnalysisResult, ResolvedSubQuery
from app.pipeline.context import PipelineContext
from app.pipeline.handlers import clarify, detail, short
from app.repositories.policy_repository import PolicyRecord
from app.schemas.analysis import Intent, StatusFilter
from app.schemas.pipeline import ConversationTurn, PipelineInput, PolicyRef

RENT = PolicyRef(policy_no="P-RENT", policy_name="청년 월세 지원")
MOVE = PolicyRef(policy_no="P-MOVE", policy_name="청년 이사비 지원")
LOAN = PolicyRef(policy_no="P-LOAN", policy_name="청년 전세대출")


def _record(policy: PolicyRef) -> PolicyRecord:
    return PolicyRecord(
        policy_no=policy.policy_no, policy_name=policy.policy_name, application_period_type_code=None,
        application_start_date=None, application_end_date=None, application_url=None,
        reference_url_1=None, reference_url_2=None, supervising_org_name=None,
    )


class FakePolicyRepository:
    def __init__(self, *policies: PolicyRef) -> None:
        self._records = {policy.policy_no: _record(policy) for policy in policies}
        self.document_requests: list[list[str]] = []

    async def get_by_ids(self, policy_nos):
        return {no: self._records[no] for no in policy_nos if no in self._records}

    async def get_documents(self, policy_nos):
        self.document_requests.append(policy_nos)
        return {no: [f"{no} 원문"] for no in policy_nos}

    async def get_region_labels(self, policy_nos):
        return {no: "전국" for no in policy_nos}


def _ctx(
    repository: FakePolicyRepository,
    targets: list[PolicyRef] | None = None,
    history: list[ConversationTurn] | None = None,
    current: list[PolicyRef] | None = None,
) -> PipelineContext:
    settings = Settings(openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test")
    deps = PipelineDeps(
        settings=settings, llm=None, retriever=None, policy_repository=repository, common_code_repository=None
    )
    sub = ResolvedSubQuery(
        standalone_question="q", target_refs=[], targets=targets or [], intent=Intent.POLICY_DETAIL,
        status_filter=StatusFilter.NONE,
    )
    ctx = PipelineContext(
        input=PipelineInput(question="q", history=history or []), deps=deps, query="q", today=date(2026, 10, 24),
        analysis=AnalysisResult(
            sub_queries=[sub], is_new_topic=False, fallback_reason=None, prompt_version="analyze_v3", model="m"
        ),
    )
    ctx.current_policies = current or []
    return ctx


async def test_detail_uses_only_targets():
    repository = FakePolicyRepository(RENT, MOVE)

    evidence = await detail.collect(_ctx(repository, targets=[MOVE, RENT]))

    # 지목한 순서가 카드 순서이고, 검색하지 않아 다른 정책이 섞이지 않음
    assert [group.policy_no for group in evidence.candidates] == ["P-MOVE", "P-RENT"]
    assert repository.document_requests == [["P-MOVE", "P-RENT"]]
    assert evidence.fixed_cards is True


async def test_detail_skips_target_without_record():
    evidence = await detail.collect(_ctx(FakePolicyRepository(RENT), targets=[MOVE, RENT]))

    assert [group.policy_no for group in evidence.candidates] == ["P-RENT"]


async def test_short_has_no_evidence():
    evidence = await short.collect(_ctx(FakePolicyRepository()))

    assert evidence.candidates == []
    assert evidence.reply is None


async def test_clarify_lists_current_policies_as_cards():
    turn = ConversationTurn(question="q", answer="a", policies=[LOAN])

    evidence = await clarify.collect(
        _ctx(FakePolicyRepository(RENT, MOVE, LOAN), history=[turn], current=[RENT, MOVE])
    )

    # 현재 정책이 화면 카드보다 우선하고, 답변 번호와 카드 번호가 같음
    assert [group.policy_no for group in evidence.candidates] == ["P-RENT", "P-MOVE"]
    assert evidence.reply == "어느 정책을 말씀하시나요?\n1. 청년 월세 지원\n2. 청년 이사비 지원\n번호나 정책 이름으로 말씀해 주세요."
    assert evidence.region_labels == {"P-RENT": "전국", "P-MOVE": "전국"}


async def test_clarify_lists_shown_cards_without_current_policy():
    turn = ConversationTurn(question="q", answer="a", policies=[RENT, LOAN])

    evidence = await clarify.collect(_ctx(FakePolicyRepository(RENT, LOAN), history=[turn]))

    assert [group.policy_no for group in evidence.candidates] == ["P-RENT", "P-LOAN"]


async def test_clarify_general_reply_without_options():
    evidence = await clarify.collect(_ctx(FakePolicyRepository()))

    assert evidence.candidates == []
    assert evidence.reply == clarify.GENERAL_REPLY
