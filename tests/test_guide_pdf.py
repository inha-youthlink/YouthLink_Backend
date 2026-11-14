from dataclasses import replace
from datetime import date, datetime

import pytest

from app.services import guide_pdf
from app.services.guide_pdf import GuideContent, render_guide_pdf

MISSING = "공고에 나와 있지 않아요. 담당 기관(서울특별시)에 문의해 주세요."

CONTENT = GuideContent(
    policy_name="청년 월세 지원",
    overview=[("담당 기관", "서울특별시"), ("신청 상태", "신청 가능 (2026-10-01 ~ 2026-11-30)")],
    profile=[("지역", "서울특별시 강남구"), ("나이", "27세")],
    observed=[("주거 상황", "월세 원룸에 혼자 거주")],
    conditions=["나이: 19~34세", "무주택자"],
    steps=[("온라인 신청서 작성", "복지로"), ("결과 통보를 기다림", "")],
    documents=["임대차계약서 사본"],
    documents_raw="임대차계약서, 월세 이체 증빙",
    cautions=["중복 수혜 불가"],
    terms=[("중위소득", "가운데 가구의 소득")],
    missing=MISSING,
    generated_at=datetime(2026, 11, 14, 20, 30),
    synced_on=date(2026, 11, 10),
    source_url="https://example.go.kr",
)


# PDF에 찍히는 글자는 모두 _clean을 거쳐, 감싸서 그려진 글자를 순서대로 모음
@pytest.fixture
def drawn(monkeypatch) -> list[str]:
    texts: list[str] = []
    original = guide_pdf._clean

    def record(text: str) -> str:
        cleaned = original(text)
        texts.append(cleaned)
        return cleaned

    monkeypatch.setattr(guide_pdf, "_clean", record)
    return texts


def test_render_full_guide(drawn):
    pdf = render_guide_pdf(CONTENT)

    assert pdf.startswith(b"%PDF")
    headings = [text for text in drawn if text[:2] in {f"{n}." for n in range(1, 10)}]
    assert headings == ["1. 내 상황", "2. 확인할 조건", "3. 신청 방법", "4. 제출 서류", "5. 주의사항", "6. 용어 설명"]
    for text in ["월세 원룸에 혼자 거주", "무주택자", "온라인 신청서 작성", "임대차계약서, 월세 이체 증빙", "중위소득"]:
        assert text in drawn
    assert "공고 확인: https://example.go.kr" in drawn
    assert "정책 정보 기준일: 2026-11-10" in drawn
    assert "만든 시각: 2026-11-14 20:30" in drawn
    assert MISSING not in drawn


# 공고에 없는 항목은 문의처 안내, 주의사항·용어는 절을 빼고 번호를 당김
def test_render_empty_sections(drawn):
    empty = replace(
        CONTENT, observed=[], conditions=[], steps=[], documents=[], documents_raw="", cautions=[], terms=[],
        synced_on=None, source_url=None,
    )

    render_guide_pdf(empty)

    headings = [text for text in drawn if text[:2] in {f"{n}." for n in range(1, 10)}]
    assert headings == ["1. 내 상황", "2. 확인할 조건", "3. 신청 방법", "4. 제출 서류"]
    assert drawn.count(MISSING) == 3
    assert "대화에서 따로 말씀하신 내용이 없어 프로필만 적었어요." in drawn
    assert not any(text.startswith(("공고 확인", "정책 정보 기준일")) for text in drawn)


# 표 칸이 길거나 띄어쓰기 없는 긴 단어여도 PDF가 만들어짐 (표 칸은 잘라 한 행이 페이지를 넘지 않음)
def test_render_long_cells():
    long_text = "복지로 홈페이지에서 신청서를 작성하고 서류를 첨부함. " * 30 + "A" * 200
    content = replace(CONTENT, steps=[(long_text, long_text)] * 5, conditions=[long_text] * 10)

    assert render_guide_pdf(content).startswith(b"%PDF")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("무주택자 ①", "무주택자 1"),
        ("㈜유스링크", "(주)유스링크"),
        ("확인 ✓ 완료 😀", "확인  완료"),
        ("□ 서류 • 원문 ※", "□ 서류 • 원문 ※"),
        ("첫 줄\r\n둘째\t줄", "첫 줄\n둘째 줄"),
    ],
)
def test_clean_replaces_unsupported_glyphs(text, expected):
    assert guide_pdf._clean(text) == expected


def test_cell_truncates_long_text():
    assert guide_pdf._cell("가" * 500) == "가" * guide_pdf.CELL_MAX_CHARS + "…"
    assert guide_pdf._cell("짧은 칸") == "짧은 칸"
