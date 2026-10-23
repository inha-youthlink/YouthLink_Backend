import uuid
from datetime import date

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.llm.client import ChatResult
from app.pipeline.context import PipelineContext
from app.pipeline.generator import CLOSED_CODE
from app.pipeline.handlers import default_rag
from app.repositories.policy_repository import PolicyRecord
from app.schemas.pipeline import Chunk, PipelineInput


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
    )


class FakeRetriever:
    async def retrieve(self, query, policy_ids, k):
        # 점수는 마감 정책이 가장 높음
        return [_chunk("P-CLOSED", 0.9), _chunk("P-A", 0.8), _chunk("P-B", 0.7), _chunk("P-C", 0.6)]


class FakePolicyRepository:
    def __init__(self) -> None:
        self.document_requests: list[list[str]] = []

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


def _ctx(llm_text: str, repository: FakePolicyRepository) -> PipelineContext:
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
    return PipelineContext(input=PipelineInput(question=question), deps=deps, query=question, today=date(2026, 10, 23))


async def test_cards_follow_status_order_and_selection():
    repository = FakePolicyRepository()

    output = await default_rag.handle(_ctx("[소개: 1,3]\n1. 정책 P-A\n2. 정책 P-C", repository))

    # 마감 정책은 점수가 가장 높아도 뒤로 밀려 후보 3개에서 빠짐
    assert repository.document_requests == [["P-A", "P-B", "P-C"]]
    assert [policy.policy_no for policy in output.policies] == ["P-A", "P-C"]
    assert output.answer == "1. 정책 P-A\n2. 정책 P-C"
    assert output.trace["cards"] == {"candidate_nos": ["P-A", "P-B", "P-C"], "selected": [1, 3], "fallback": False}
    assert output.trace["prompt_version"] == "generate_v2"


async def test_no_cards_when_nothing_selected():
    output = await default_rag.handle(_ctx("[소개: ]\n관련 정책을 찾지 못했어요.", FakePolicyRepository()))

    assert output.policies == []
    assert output.answer == "관련 정책을 찾지 못했어요."


async def test_all_candidates_shown_when_first_line_missing():
    output = await default_rag.handle(_ctx("첫 줄 없이 답변", FakePolicyRepository()))

    assert [policy.policy_no for policy in output.policies] == ["P-A", "P-B", "P-C"]
    assert output.answer == "첫 줄 없이 답변"
    assert output.trace["cards"]["fallback"] is True
