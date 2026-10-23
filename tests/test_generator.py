from datetime import date

import pytest

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.llm.client import ChatResult
from app.pipeline import generator
from app.pipeline.context import PipelineContext
from app.pipeline.generator import parse_card_line
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord
from app.schemas.policy_info import CLOSED_CODE
from app.schemas.pipeline import PipelineInput


@pytest.mark.parametrize(
    ("text", "numbers", "body"),
    [
        ("[소개: 1,3,4]\n답변", [1, 3, 4], "답변"),
        ("[소개: 1, 3]\n\n답변", [1, 3], "답변"),
        ("\n[소개:2]\n답변", [2], "답변"),
        ("[소개: ]\n안녕하세요", [], "안녕하세요"),
        ("[소개: 없음]\n안녕하세요", [], "안녕하세요"),
        ("[소개: 1번, 3번]\n답변", [1, 3], "답변"),
        # LLM은 빼기만 할 수 있어 순서를 바꿔도 후보 순서로 되돌리고, 중복과 없는 번호는 버림
        ("[소개: 3,1,1,9,0]\n답변", [1, 3], "답변"),
    ],
)
def test_parse_card_line(text, numbers, body):
    result = parse_card_line(text, candidate_count=5)

    assert result.numbers == numbers
    assert result.body == body


@pytest.mark.parametrize(
    "text",
    [
        "답변만 있음",
        "1. 정책 [소개: 1]\n답변",
        "[소개: 1,2] 첫 줄에 답변이 이어짐",
    ],
)
def test_parse_card_line_without_valid_first_line(text):
    result = parse_card_line(text, candidate_count=5)

    assert result.numbers is None
    assert result.body == text.strip()


class FakeLLM:
    chat_model = "fake-chat-model"

    def __init__(self, text: str) -> None:
        self._text = text
        self.messages: list[dict[str, str]] = []

    async def chat(self, messages):
        self.messages = messages
        return ChatResult(text=self._text, prompt_tokens=100, completion_tokens=20)


class FakeCommonCodes:
    async def get_names(self, codes):
        return {}


def _record(policy_no: str, name: str, code: str | None = None, **links) -> PolicyRecord:
    return PolicyRecord(
        policy_no=policy_no,
        policy_name=name,
        application_period_type_code=code,
        application_start_date=None,
        application_end_date=None,
        application_url=links.get("application_url"),
        reference_url_1=links.get("reference_url_1"),
        reference_url_2=links.get("reference_url_2"),
        supervising_org_name=links.get("org"),
    )


def _ctx(llm: FakeLLM) -> PipelineContext:
    settings = Settings(openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test")
    deps = PipelineDeps(
        settings=settings, llm=llm, retriever=None, policy_repository=None, common_code_repository=FakeCommonCodes()
    )
    ctx = PipelineContext(
        input=PipelineInput(question="서울 월세 지원 알려줘"), deps=deps, query="서울 월세 지원 알려줘", today=date(2026, 10, 23)
    )
    ctx.candidates = [PolicyGroup("P-RENT", 0.9, []), PolicyGroup("P-LOAN", 0.8, [])]
    ctx.policies = {
        "P-RENT": _record(
            "P-RENT",
            "청년 월세 지원",
            application_url="www.example.go.kr",
            reference_url_1="https://www.example.go.kr",
            reference_url_2="https://www.example.go.kr/rent",
            org="서울특별시 주택정책과",
        ),
        "P-LOAN": _record("P-LOAN", "청년 전세 이자 지원", CLOSED_CODE),
    }
    ctx.documents = {"P-RENT": ["정책명: 청년 월세 지원\n적용 지역: 서울특별시", "지원 내용: 월 20만 원"]}
    return ctx


async def test_generate_uses_whole_documents_of_candidates():
    llm = FakeLLM("[소개: 2]\n전세 이자 지원은 마감되었어요.")

    result = await generator.generate(_ctx(llm))

    user_message = llm.messages[1]["content"]
    # 후보 순서가 근거 번호이고, 청크가 없는 정책도 번호가 밀리지 않도록 블록을 만듦
    assert "<정책 1> 청년 월세 지원" in user_message
    assert "적용 지역: 서울특별시\n지원 내용: 월 20만 원" in user_message
    # scheme이 빠진 신청 링크는 https를 붙이고, 신청 링크와 같은 참고 링크는 건너뜀
    assert (
        "<정책 1> 청년 월세 지원\n[신청 상태] 정보 없음\n[신청 링크] https://www.example.go.kr\n"
        "[참고 링크] https://www.example.go.kr/rent\n[담당 기관] 서울특별시 주택정책과\n[정책 정보]"
    ) in user_message
    assert (
        "<정책 2> 청년 전세 이자 지원\n[신청 상태] 마감\n[신청 링크] 정보 없음\n[참고 링크] 정보 없음\n"
        "[담당 기관] 정보 없음\n[정책 정보]\n정보 없음"
    ) in user_message
    assert result.card_numbers == [2]
    assert result.answer == "전세 이자 지원은 마감되었어요."
    assert result.prompt_version == "generate_v3"
    assert len(result.contexts) == 2
