from datetime import date

import pytest

from app.core.config import Settings
from app.core.dependencies import PipelineDeps
from app.llm.client import ChatUsage, LLMStreamError
from app.pipeline import generator
from app.pipeline.conditions import RegionIndex, build_conditions
from app.pipeline.context import PipelineContext
from app.pipeline.evidence import AnswerKind, Evidence
from app.pipeline.generator import (
    AnswerDelta,
    CardsDecided,
    GenerationResult,
    parse_card_line,
)
from app.pipeline.grouping import PolicyGroup
from app.repositories.policy_repository import PolicyRecord, RegionRow
from app.schemas.pipeline import PipelineInput, Profile
from app.schemas.policy_info import CLOSED_CODE


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


CHUNK = 3


class FakeLLM:
    chat_model = "fake-chat-model"

    def __init__(self, text: str, chunk: int = CHUNK, fail_after: int | None = None) -> None:
        self._text = text
        self._chunk = chunk
        # 이 조각 수만큼 보낸 뒤 실패해, 스트림이 끝나기 전에 나간 이벤트를 확인함
        self._fail_after = fail_after
        self.messages: list[dict[str, str]] = []

    # 실제 스트림처럼 몇 글자씩 나눠 보내 첫 줄이 조각 경계에 걸리는 경우도 거침
    async def chat_stream(self, messages):
        self.messages = messages
        for sent, start in enumerate(range(0, len(self._text), self._chunk)):
            if sent == self._fail_after:
                raise LLMStreamError("중간 실패")
            yield self._text[start:start + self._chunk]
        yield ChatUsage(prompt_tokens=100, completion_tokens=20)


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


def _ctx(llm: FakeLLM, kind: AnswerKind = AnswerKind.RECOMMEND, fixed_cards: bool = False) -> PipelineContext:
    settings = Settings(openai_api_key="test", database_url="postgresql+asyncpg://test@localhost/test")
    deps = PipelineDeps(
        settings=settings, llm=llm, retriever=None, policy_repository=None, common_code_repository=FakeCommonCodes()
    )
    ctx = PipelineContext(
        input=PipelineInput(question="서울 월세 지원 알려줘"), deps=deps, query="서울 월세 지원 알려줘", today=date(2026, 10, 23)
    )
    ctx.answer_kind = kind
    ctx.evidence = Evidence(
        candidates=[PolicyGroup("P-RENT", 0.9, []), PolicyGroup("P-LOAN", 0.8, [])],
        policies={
            "P-RENT": _record(
                "P-RENT",
                "청년 월세 지원",
                application_url="www.example.go.kr",
                reference_url_1="https://www.example.go.kr",
                reference_url_2="https://www.example.go.kr/rent",
                org="서울특별시 주택정책과",
            ),
            "P-LOAN": _record("P-LOAN", "청년 전세 이자 지원", CLOSED_CODE),
        },
        documents={"P-RENT": ["정책명: 청년 월세 지원\n적용 지역: 서울특별시", "지원 내용: 월 20만 원"]},
        fixed_cards=fixed_cards,
    )
    return ctx


# 스트림을 끝까지 모아 결과만 봄 (한 번에 받는 /internal과 같은 방식)
async def _generate(ctx: PipelineContext) -> GenerationResult:
    events = [event async for event in generator.generate_stream(ctx)]
    assert isinstance(events[-1], GenerationResult)
    return events[-1]


def _system_prompt(llm: FakeLLM) -> str:
    return llm.messages[0]["content"]


async def test_generate_uses_whole_documents_of_candidates():
    llm = FakeLLM("[소개: 2]\n전세 이자 지원은 마감되었어요.")

    result = await _generate(_ctx(llm))

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
    assert result.prompt_version == "answer_common_v2+answer_cards_v2+answer_recommend_v1"
    assert len(result.contexts) == 2


async def test_search_detail_keeps_card_line():
    llm = FakeLLM("[소개: 1]\n청년 월세 지원은 월 20만 원을 지원해요.")

    result = await _generate(_ctx(llm, AnswerKind.DETAIL))

    # 처음 묻는 정책 이름은 검색 후보 중 하나를 LLM이 골라야 해서 첫 줄 형식을 유지함
    assert result.prompt_version == "answer_common_v2+answer_cards_v2+answer_detail_v1"
    assert "# 출력 형식" in _system_prompt(llm)
    assert "# 신청 방법 안내" in _system_prompt(llm)
    assert result.card_numbers == [1]


async def test_fixed_cards_skip_card_line():
    llm = FakeLLM("청년 월세 지원은 월 20만 원을 지원해요.")

    result = await _generate(_ctx(llm, AnswerKind.DETAIL, fixed_cards=True))

    assert result.prompt_version == "answer_common_v2+answer_detail_v1"
    assert "[소개:" not in _system_prompt(llm)
    assert result.card_numbers == [1, 2]
    assert result.answer == "청년 월세 지원은 월 20만 원을 지원해요."


async def test_short_answer_has_no_evidence():
    llm = FakeLLM("안녕하세요! 궁금한 청년 정책이 있으면 물어봐 주세요.")
    ctx = _ctx(llm, AnswerKind.SHORT, fixed_cards=True)
    ctx.evidence = Evidence(fixed_cards=True)

    result = await _generate(ctx)

    assert result.prompt_version == "answer_short_v1"
    assert llm.messages[1]["content"] == "[질문]\n서울 월세 지원 알려줘"
    assert result.contexts == []
    assert result.card_numbers == []


async def test_reply_skips_llm():
    llm = FakeLLM("호출되면 안 됨")
    ctx = _ctx(llm, AnswerKind.CLARIFY, fixed_cards=True)
    ctx.evidence = Evidence(
        candidates=ctx.evidence.candidates, policies=ctx.evidence.policies, fixed_cards=True, reply="어느 정책을 말씀하시나요?"
    )

    result = await _generate(ctx)

    assert llm.messages == []
    assert result.answer == "어느 정책을 말씀하시나요?"
    assert result.prompt_version is None
    assert result.card_numbers == [1, 2]
    assert (result.prompt_tokens, result.completion_tokens) == (0, 0)


async def test_profile_region_and_conditions_shown_for_filtered_evidence():
    llm = FakeLLM("[소개: 1]\n월세 지원이 있어요.")
    ctx = _ctx(llm)
    profile = Profile(region_code="41117")
    ctx.input = PipelineInput(question="월세 지원 알려줘", profile=profile)
    ctx.regions = RegionIndex([
        RegionRow("41", "경기도", "수원시 권선구", "41113"), RegionRow("41", "경기도", "수원시 영통구", "41117"),
    ])
    ctx.conditions = build_conditions(None, profile, ctx.regions, {})
    ctx.evidence = Evidence(**{**ctx.evidence.__dict__, "filtered": True})

    await _generate(ctx)

    user_message = llm.messages[1]["content"]
    # 구가 있는 시는 시 단위로 보여 줌
    assert "[사용자 프로필]\n지역: 경기도 수원시" in user_message
    assert "[검색 조건]\n지역: 경기도 수원시" in user_message


async def _events(ctx: PipelineContext) -> list:
    events = []
    try:
        async for event in generator.generate_stream(ctx):
            events.append(event)
    except LLMStreamError:
        pass
    return events


# 조각을 어떻게 나눠 받아도 한 번에 받아 파싱한 결과와 같고, 내보낸 조각을 이으면 답변과 같음
@pytest.mark.parametrize("chunk", [1, 2, 5, 1000])
@pytest.mark.parametrize(
    "text",
    [
        "[소개: 2]\n전세 이자 지원은 마감되었어요.",
        "\n[소개:1, 2]\n\n  첫 문장.  \n\n둘째 문장  ",
        "[소개: 없음]\n안녕하세요",
        "[소개: 1]",
        "첫 줄 없이 바로 답변하고 끝에 공백   ",
        "[중요] 대괄호로 시작하지만 카드 줄이 아님\n다음 줄",
        "[소개: 1,2] 첫 줄에 답변이 이어짐",
        "가" * 50 + "\n[소개: 1]",
        "[소개: 1]" + " " * 40 + "\n줄 끝 공백이 긴 카드 줄",
    ],
)
async def test_stream_matches_whole_answer(text, chunk):
    expected = parse_card_line(text, candidate_count=2)

    events = await _events(_ctx(FakeLLM(text, chunk=chunk)))

    result = events[-1]
    assert isinstance(result, GenerationResult)
    assert (result.card_numbers, result.answer) == (expected.numbers, expected.body)
    assert "".join(e.text for e in events if isinstance(e, AnswerDelta)) == expected.body
    assert (result.prompt_tokens, result.completion_tokens) == (100, 20)


# 카드는 한 번, 항상 첫 본문 조각보다 먼저
async def test_stream_sends_cards_once_before_first_delta():
    events = await _events(_ctx(FakeLLM("[소개: 2]\n전세 이자 지원은 마감되었어요.")))

    kinds = [type(event) for event in events]
    assert kinds.count(CardsDecided) == 1
    assert kinds.index(CardsDecided) < kinds.index(AnswerDelta)
    assert events[kinds.index(CardsDecided)] == CardsDecided([2])


# 40자가 넘도록 줄바꿈이 없으면 스트림이 끝나기 전에 카드 줄이 아니라고 보고 본문을 보냄
async def test_long_first_line_is_not_held():
    llm = FakeLLM("[" + "가" * 44 + "\n나머지", chunk=45, fail_after=1)

    events = await _events(_ctx(llm))

    assert events[0] == CardsDecided(None)
    assert isinstance(events[1], AnswerDelta)


# 카드가 정해진 답변은 LLM 첫 조각을 받기 전에 카드를 보냄
async def test_fixed_cards_sent_before_llm_output():
    events = await _events(_ctx(FakeLLM("답변", fail_after=0), AnswerKind.DETAIL, fixed_cards=True))

    assert events == [CardsDecided([1, 2])]


async def test_reply_streams_as_one_delta():
    ctx = _ctx(FakeLLM("호출되면 안 됨"), AnswerKind.CLARIFY, fixed_cards=True)
    ctx.evidence = Evidence(candidates=ctx.evidence.candidates, policies=ctx.evidence.policies, reply="어느 정책인가요?")

    events = await _events(ctx)

    assert events[:2] == [CardsDecided([1, 2]), AnswerDelta("어느 정책인가요?")]
    assert isinstance(events[2], GenerationResult)
