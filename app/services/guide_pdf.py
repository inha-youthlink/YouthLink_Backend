import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

# 신청 가이드 PDF 조립. 내용은 guide_service가 정하고 여기서는 배치만 함 (LLM·DB 없음)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FAMILY = "NanumGothic"
_FONT_FILES = {"": "NanumGothic-Regular.ttf", "B": "NanumGothic-Bold.ttf"}

TEXT_COLOR = (33, 37, 41)
MUTED_COLOR = (108, 117, 125)
ACCENT_COLOR = (37, 99, 235)
HEADER_FILL = (235, 241, 252)
LABEL_FILL = (246, 248, 250)
NOTICE_FILL = (255, 246, 230)
SUMMARY_FILL = (240, 245, 255)
ALERT_COLOR = (194, 65, 12)
LINE_HEIGHT = 6
# 제목(약 10mm) + 내용 3줄
HEADING_KEEP_HEIGHT = 30
NOTICE_KEEP_HEIGHT = 45
CELL_PADDING = (1.5, 2)
# fpdf2 표는 한 행을 여러 페이지로 나누지 못해, 한 칸이 페이지를 넘지 않을 길이로 자름 (단계·용어 설명은 보통 100자 안쪽)
CELL_MAX_CHARS = 400

NOTICE_TEXT = (
    "이 가이드는 공고 데이터와 대화 내용을 바탕으로 자동으로 만든 참고 자료입니다. "
    "자격·금액·기간·서류는 바뀔 수 있으니 신청 전에 반드시 공고 원문과 담당 기관에서 확인해 주세요. "
    "이 문서는 자격을 판정하거나 선정을 보장하지 않습니다."
)


@dataclass(frozen=True)
class GuideContent:
    policy_name: str
    # (항목, 값). 기관·신청 상태·사이트처럼 공고 데이터에서 코드가 만든 값
    overview: list[tuple[str, str]]
    profile: list[tuple[str, str]]
    observed: list[tuple[str, str]]
    conditions: list[str]
    # (할 일, 어디서)
    steps: list[tuple[str, str]]
    documents: list[str]
    documents_raw: str
    # 공고에서 LLM이 뽑은 주의사항
    cautions: list[str]
    terms: list[tuple[str, str]]
    # 공고에 없는 항목에 쓰는 안내 (문의처 포함)
    missing: str
    generated_at: datetime
    synced_on: date | None = None
    source_url: str | None = None
    # 코드가 정한 경고 (프로필 밖 정책, 마감 임박·마감·예정, 선착순). 첫 쪽 요약에 둠
    alerts: list[str] = field(default_factory=list)


# 프로필 한 줄 요약에서 빼는 값 (알려 주는 것이 없음)
_EMPTY_PROFILE_VALUES = frozenset({"제한없음", "없음", "정보 없음"})
# 값만으로는 무엇인지 모르는 항목은 이름을 붙임
_LABELED_PROFILE_ITEMS = frozenset({"연소득", "전공", "특화 대상"})


# 한눈에 보이도록 할 일(신청 방법·서류)을 앞에, 참고 정보(내 상황·용어)를 뒤에 둠
def render_guide_pdf(content: GuideContent) -> bytes:
    pdf = _GuidePdf()
    # PDF 뷰어 탭에 보이는 제목
    pdf.set_title(_clean(f"{content.policy_name} 신청 가이드"))
    pdf.add_page()

    pdf.set_font(FONT_FAMILY, "", 10)
    pdf.set_text_color(*ACCENT_COLOR)
    pdf.write_line("신청 가이드")
    pdf.set_text_color(*TEXT_COLOR)
    pdf.set_font(FONT_FAMILY, "B", 18)
    pdf.write_line(content.policy_name, height=10)
    pdf.set_font(FONT_FAMILY, "", 10)
    pdf.ln(2)
    pdf.summary(content)

    sections = _Sections(pdf)

    sections.start("신청 방법")
    if content.steps:
        pdf.steps_table(content.steps)
    else:
        pdf.paragraph(content.missing)

    sections.start("제출 서류")
    if content.documents:
        pdf.items(content.documents, mark="□")
        if content.documents_raw:
            pdf.paragraph("서류별 조건과 발급 기준은 맨 뒤 부록(공고 원문)에서 확인해 주세요.", muted=True)
    elif content.documents_raw:
        pdf.paragraph(content.documents_raw)
    else:
        pdf.paragraph(content.missing)

    sections.start("확인할 조건")
    pdf.paragraph("공고에 적힌 조건입니다. 해당하는지 직접 확인해 주세요.", muted=True)
    if content.conditions:
        pdf.items(content.conditions, mark="□")
    else:
        pdf.paragraph(content.missing)

    # 주의사항·용어는 없으면 절을 빼고 번호를 당김
    if content.cautions:
        sections.start("주의사항")
        pdf.items(content.cautions, mark="•")

    sections.start("내 상황")
    pdf.paragraph(_profile_line(content.profile) or "프로필 정보가 없어요.")
    if content.observed:
        pdf.subheading("대화에서 말씀하신 내용")
        pdf.key_value_table(content.observed)
    else:
        pdf.paragraph("대화에서 따로 말씀하신 내용이 없어 프로필만 적었어요.", muted=True)

    if content.terms:
        sections.start("용어 설명")
        pdf.paragraph("공고에 나온 용어의 일반적인 뜻입니다. 이 정책에서의 기준은 공고를 따릅니다.", muted=True)
        pdf.key_value_table(content.terms)

    pdf.ln(4)
    pdf.notice(content)

    # 체크리스트와 내용이 거의 같아 본문 중간에 두면 길이만 늘어나 확인용으로 맨 뒤에 둠
    if content.documents and content.documents_raw:
        pdf.heading("부록. 제출 서류 공고 원문")
        pdf.paragraph(content.documents_raw, muted=True)
    return bytes(pdf.output())


def _profile_line(profile: list[tuple[str, str]]) -> str:
    values = [
        f"{label} {value}" if label in _LABELED_PROFILE_ITEMS else value
        for label, value in profile
        if value and value not in _EMPTY_PROFILE_VALUES
    ]
    return " · ".join(values)


class _Sections:
    def __init__(self, pdf: "_GuidePdf") -> None:
        self._pdf = pdf
        self._number = 0

    def start(self, title: str) -> None:
        self._number += 1
        self._pdf.heading(f"{self._number}. {title}")


class _GuidePdf(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        for style, filename in _FONT_FILES.items():
            self.add_font(FONT_FAMILY, style, str(FONT_DIR / filename))
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(True, margin=18)
        self.set_text_color(*TEXT_COLOR)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(FONT_FAMILY, "", 8)
        self.set_text_color(*MUTED_COLOR)
        self.cell(0, 6, f"YouthLink 신청 가이드 · {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(*TEXT_COLOR)

    # fpdf2의 multi_cell은 기본으로 커서를 오른쪽 끝에 둬 다음 줄이 그려지지 않아 항상 다음 줄 왼쪽으로 옮김
    # 기본 양쪽 정렬은 긴 URL이 있는 줄의 낱말 사이를 크게 벌려 왼쪽 정렬로 씀
    def write_line(self, text: str, height: float = LINE_HEIGHT, fill: bool = False) -> None:
        self.multi_cell(0, height, _clean(text), fill=fill, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def heading(self, text: str) -> None:
        # 제목만 쪽 끝에 남고 내용이 다음 쪽으로 넘어가지 않게, 내용 몇 줄이 들어갈 자리가 없으면 쪽을 넘김
        if self.get_y() + HEADING_KEEP_HEIGHT > self.page_break_trigger:
            self.add_page()
        else:
            self.ln(5)
        self.set_font(FONT_FAMILY, "B", 13)
        self.write_line(text, height=8)
        self.set_draw_color(*ACCENT_COLOR)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_font(FONT_FAMILY, "", 10)

    def subheading(self, text: str) -> None:
        self.ln(1)
        self.set_font(FONT_FAMILY, "B", 10.5)
        self.write_line(text)
        self.set_font(FONT_FAMILY, "", 10)

    def paragraph(self, text: str, muted: bool = False) -> None:
        if muted:
            self.set_text_color(*MUTED_COLOR)
        self.write_line(text)
        self.set_text_color(*TEXT_COLOR)
        self.ln(1)

    def items(self, values: list[str], mark: str) -> None:
        indent = 5
        for value in values:
            self.cell(indent, LINE_HEIGHT, mark)
            self.multi_cell(0, LINE_HEIGHT, _clean(value), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def key_value_table(self, rows: list[tuple[str, str]]) -> None:
        self.set_draw_color(210, 214, 220)
        with self.table(
            col_widths=(28, 72),
            first_row_as_headings=False,
            text_align="LEFT",
            line_height=LINE_HEIGHT,
            padding=CELL_PADDING,
        ) as table:
            label_style = FontFace(emphasis="BOLD", fill_color=LABEL_FILL)
            for label, value in rows:
                row = table.row()
                row.cell(_cell(label), style=label_style)
                row.cell(_cell(value))
        self.ln(2)

    # 어디서 칸에 방법별 조건(나이별 신청처 등)이 길게 들어가, 칸을 나누지 않고 할 일(굵게) 아래 줄에 둠
    def steps_table(self, steps: list[tuple[str, str]]) -> None:
        self.set_draw_color(210, 214, 220)
        with self.table(
            col_widths=(10, 90),
            headings_style=FontFace(emphasis="BOLD", fill_color=HEADER_FILL),
            text_align=("CENTER", "LEFT"),
            line_height=LINE_HEIGHT,
            padding=CELL_PADDING,
            markdown=True,
        ) as table:
            heading = table.row()
            for title in ("단계", "할 일"):
                heading.cell(title)
            for number, (action, where) in enumerate(steps, start=1):
                row = table.row()
                row.cell(str(number))
                text = f"**{_markdown_safe(_cell(action))}**"
                if where.strip():
                    text += f"\n{_markdown_safe(_cell(where))}"
                row.cell(text)
        self.ln(2)

    # 첫 쪽만 봐도 신청 상태·신청처·준비할 것·경고가 보이게 함
    def summary(self, content: GuideContent) -> None:
        steps = f"할 일 {len(content.steps)}단계" if content.steps else "신청 방법 공고에 없음"
        documents = f"제출 서류 {len(content.documents)}개" if content.documents else "제출 서류 목록 공고에 없음"
        rows = [*content.overview, ("준비할 것", f"{steps} · {documents}")]
        self.set_draw_color(*SUMMARY_FILL)
        with self.table(
            col_widths=(24, 76),
            first_row_as_headings=False,
            text_align="LEFT",
            line_height=LINE_HEIGHT,
            padding=CELL_PADDING,
            cell_fill_color=SUMMARY_FILL,
            cell_fill_mode="ALL",
        ) as table:
            label_style = FontFace(emphasis="BOLD")
            for label, value in rows:
                row = table.row()
                row.cell(_cell(label), style=label_style)
                row.cell(_cell(value))
        if content.alerts:
            self.ln(1)
            self.set_text_color(*ALERT_COLOR)
            self.set_font(FONT_FAMILY, "B", 10)
            self.items(content.alerts, mark="!")
            self.set_font(FONT_FAMILY, "", 10)
            self.set_text_color(*TEXT_COLOR)
        self.ln(1)

    def notice(self, content: GuideContent) -> None:
        # 안내 상자가 두 쪽에 나뉘지 않게 함 (보통 6~7줄)
        if self.get_y() + NOTICE_KEEP_HEIGHT > self.page_break_trigger:
            self.add_page()
        self.set_fill_color(*NOTICE_FILL)
        self.set_font(FONT_FAMILY, "B", 10)
        self.write_line("꼭 확인해 주세요", fill=True)
        self.set_font(FONT_FAMILY, "", 9.5)
        lines = [NOTICE_TEXT]
        if content.source_url:
            lines.append(f"공고 확인: {content.source_url}")
        if content.synced_on:
            lines.append(f"정책 정보 기준일: {content.synced_on.isoformat()}")
        lines.append(f"만든 시각: {content.generated_at.strftime('%Y-%m-%d %H:%M')}")
        for line in lines:
            self.write_line(line, fill=True)


@lru_cache
def _supported_codepoints() -> frozenset[int]:
    codepoints: set[int] = set()
    for filename in _FONT_FILES.values():
        codepoints.update(TTFont(str(FONT_DIR / filename)).getBestCmap())
    return frozenset(codepoints)


# 단계 표만 굵은 글씨를 쓰려고 마크다운을 켜, 원문의 표시 기호(**, __, --, ~~)가 서식으로 읽히지 않게 하나로 줄임
def _markdown_safe(text: str) -> str:
    for marker in ("**", "__", "--", "~~"):
        while marker in text:
            text = text.replace(marker, marker[0])
    return text


def _cell(text: str) -> str:
    cleaned = _clean(text)
    return cleaned if len(cleaned) <= CELL_MAX_CHARS else cleaned[:CELL_MAX_CHARS].rstrip() + "…"


# 폰트에 없는 글자는 빈칸으로 찍혀, 호환 문자로 바꿀 수 있으면 바꾸고(①→1, ㈜→(주)) 아니면 뺌
def _clean(text: str) -> str:
    supported = _supported_codepoints()
    result = []
    for char in text.replace("\r\n", "\n").replace("\t", " "):
        if char == "\n" or ord(char) in supported:
            result.append(char)
            continue
        normalized = unicodedata.normalize("NFKC", char)
        if normalized != char and all(ord(c) in supported for c in normalized):
            result.append(normalized)
    return "".join(result).strip()
