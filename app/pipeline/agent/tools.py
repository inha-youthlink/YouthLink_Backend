import asyncio
import re
from dataclasses import dataclass, field, replace
from datetime import date
from time import perf_counter
from typing import Any

from app.core.dependencies import PipelineDeps
from app.llm.client import ToolCall
from app.pipeline.conditions import SearchConditions
from app.pipeline.generator import STATUS_LABELS
from app.pipeline.grouping import group_by_policy
from app.repositories.policy_repository import PolicyBriefRecord, PolicyDetailRecord
from app.schemas.analysis import Category
from app.schemas.policy_info import (
    CATEGORY_NAMES,
    OLD_MIDDLE_CATEGORY_NAMES,
    ApplicationStatus,
    application_status,
    category_of,
    middle_category_names,
)

# 추천 에이전트 도구 (계약 4.8). 읽기만 함. 프로필 1차 필터는 인자로 끌 수 없게 서버가 항상 검

# 칩 글자와 같은 이름으로 분야를 받음 (시작 질문의 칩이 그대로 사용자 답으로 옴)
AGENT_CATEGORIES = {
    "일자리": Category.JOB,
    "주거": Category.HOUSING,
    "교육": Category.EDUCATION,
    "복지·문화": Category.WELFARE_CULTURE,
    "참여·권리": Category.PARTICIPATION,
}
_CATEGORY_LABELS = {category: label for label, category in AGENT_CATEGORIES.items()}

# 후보 10개 × 한 줄(약 150자)이면 도구 결과가 1,500자 안팎. 상세는 get_policies에서만 (11-22 정함)
SEARCH_CANDIDATES = 10
DETAIL_LIMIT = 5
SUMMARY_CHARS = 100
DETAIL_FIELD_CHARS = 300
# 이보다 짧거나 정책 이름과 같은 설명은 무엇을 하는 사업인지 알 수 없어 지원 내용을 씀 (178개, 0절 확인)
MIN_DESCRIPTION_CHARS = 20
_LABEL = re.compile(r"P(\d+)")
# 공고 원문의 글머리 기호. 한 줄 요약에서는 띄어쓰기로 바꿈
_BULLETS = re.compile(r"[❍○●◦•□■◆◇▶▷※ㆍ·\-–]+\s+|\s+")

SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "search_policies",
    "description": "사용자 프로필에 맞는 청년 정책 안에서 찾는다. 후보 최대 10개와 세부 분류별 정책 수를 돌려준다.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "사용자가 원하는 것만 짧게. 프로필 값은 넣지 않음"},
            "category": {"type": ["string", "null"], "enum": [*AGENT_CATEGORIES, None]},
            "sub_categories": {"type": "array", "items": {"type": "string"}, "description": "이전 결과의 세부 분류 이름"},
            "include_closed": {"type": "boolean", "description": "마감된 정책도 원할 때만 true"},
            "local_only": {"type": "boolean", "description": "전국 정책 말고 지역 정책만 원할 때만 true"},
        },
        "required": ["query", "category", "sub_categories", "include_closed", "local_only"],
        "additionalProperties": False,
    },
    "strict": True,
}

DETAIL_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "get_policies",
    "description": "이번 대화에서 본 정책(최대 5개)의 지원 내용, 자격, 신청 정보를 한 번에 본다.",
    "parameters": {
        "type": "object",
        "properties": {
            "policies": {"type": "array", "items": {"type": "string"}, "description": "정책 번호 (예: P3)"},
        },
        "required": ["policies"],
        "additionalProperties": False,
    },
    "strict": True,
}

AGENT_TOOLS = [SEARCH_TOOL, DETAIL_TOOL]


# 실제 정책 번호(20자리)는 모델이 옮겨 쓰다 틀리기 쉬워 대화 안에서 짧은 번호(P1, P2…)로 부름
# 번호는 대화 전체에서 이어져 이전 턴의 정책도 같은 번호. 목록은 답변 metadata에 저장해 다음 턴에 이어 씀
class SeenPolicies:
    def __init__(self, policy_nos: list[str] | None = None) -> None:
        self._nos: list[str] = []
        self._index: dict[str, int] = {}
        for policy_no in policy_nos or []:
            self.label(policy_no)

    @property
    def policy_nos(self) -> list[str]:
        return list(self._nos)

    def label(self, policy_no: str) -> str:
        if policy_no not in self._index:
            self._index[policy_no] = len(self._nos)
            self._nos.append(policy_no)
        return f"P{self._index[policy_no] + 1}"

    # 본 적 없는 번호는 None. 모델이 지어낸 번호를 카드나 조회에 쓰지 못하게 함
    def resolve(self, label: str) -> str | None:
        match = _LABEL.fullmatch(label.strip().upper())
        if match is None:
            return None
        index = int(match.group(1)) - 1
        return self._nos[index] if 0 <= index < len(self._nos) else None


# metadata.tool_calls에 남기는 도구 호출 한 번 (평가에서 무엇을 찾고 무엇을 봤는지 확인)
@dataclass
class ToolRecord:
    name: str
    arguments: dict[str, Any]
    latency_ms: int = 0
    filtered_policy_count: int | None = None
    policy_nos: list[str] = field(default_factory=list)
    error: str | None = None

    def to_trace(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "latency_ms": self.latency_ms,
            "filtered_policy_count": self.filtered_policy_count,
            "policy_nos": self.policy_nos,
            "error": self.error,
        }


class AgentTools:
    def __init__(self, deps: PipelineDeps, conditions: SearchConditions, seen: SeenPolicies, today: date) -> None:
        self._deps = deps
        self._conditions = conditions
        self._seen = seen
        self._today = today
        self.records: list[ToolRecord] = []

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        record = ToolRecord(name, arguments)
        self.records.append(record)
        started = perf_counter()
        try:
            if name == SEARCH_TOOL["name"]:
                return await self._search(arguments, record)
            if name == DETAIL_TOOL["name"]:
                return await self._details(arguments, record)
            record.error = "unknown_tool"
            return f"없는 도구: {name}"
        finally:
            record.latency_ms = int((perf_counter() - started) * 1000)

    async def _search(self, arguments: dict[str, Any], record: ToolRecord) -> str:
        repository = self._deps.policy_repository
        category = AGENT_CATEGORIES.get(str(arguments.get("category")))
        large = tuple(sorted(CATEGORY_NAMES[category])) if category else ()
        base = self._conditions.to_filter()
        base = replace(
            base,
            exclude_closed=base.exclude_closed or not arguments.get("include_closed", False),
            large_categories=large,
        )
        category_nos = await repository.filter_policy_nos(base, self._today)
        counts = await repository.count_middle_categories(category_nos, list(large)) if large else {}
        merged = _merge_counts(counts)
        subs = _sub_categories(arguments.get("sub_categories"))
        # 모델이 선택지 글자("주택·거주지", "미래역량 강화")를 넘기는 일이 잦아 띄어쓰기·"·"·"및"을 빼고 맞춤
        # 그래도 그 분야의 세부 분류가 아니면 0개가 되므로 빼고 알림
        unknown: list[str] = []
        if merged:
            known = {_sub_key(name): name for name, _ in merged}
            resolved = [known.get(_sub_key(sub)) for sub in subs]
            unknown = [sub for sub, name in zip(subs, resolved, strict=True) if name is None]
            subs = list(dict.fromkeys(name for name in resolved if name))
        policy_nos = category_nos
        if subs and category_nos:
            names = tuple(sorted(set().union(*(middle_category_names(sub) for sub in subs))))
            policy_nos = await repository.filter_policy_nos(replace(base, middle_categories=names), self._today)
        record.filtered_policy_count = len(policy_nos)

        header = [f"조건에 맞는 정책 {len(policy_nos)}개"]
        if merged:
            header.append("세부 분류: " + " / ".join(f"{name} {n}" for name, n in merged))
        if unknown:
            header.append(f"세부 분류 이름이 아니라 빼고 찾음: {', '.join(unknown)} (위 이름을 그대로 쓸 것)")
        if not policy_nos:
            return "\n".join([*header, "후보 없음. 세부 분류나 분야를 넓혀 다시 찾거나, 맞는 정책이 없다고 답할 것"])

        query = str(arguments.get("query") or "").strip() or (_CATEGORY_LABELS[category] if category else "청년 지원")
        chunks = await self._deps.retriever.retrieve(query, policy_nos, self._deps.settings.top_k)
        groups = group_by_policy(chunks)
        briefs = await repository.get_briefs([group.policy_no for group in groups])
        regions = await repository.get_region_labels(list(briefs))
        picked = _pick(groups, briefs, regions, bool(arguments.get("local_only")))
        record.policy_nos = [brief.record.policy_no for brief in picked]

        header[0] += f" 중 관련도 순 {len(picked)}개 (이름이 같은 정책은 하나만)"
        lines = [
            self._brief_line(brief, regions.get(brief.record.policy_no), category) for brief in picked
        ]
        return "\n".join([*header, "", *lines]) if lines else "\n".join([*header, "검색 결과 없음"])

    async def _details(self, arguments: dict[str, Any], record: ToolRecord) -> str:
        labels = [str(label) for label in arguments.get("policies") or []]
        notes: list[str] = []
        policy_nos: list[str] = []
        for label in labels:
            policy_no = self._seen.resolve(label)
            if policy_no is None:
                notes.append(f"{label}: 이번 대화에서 본 정책이 아님")
            elif policy_no not in policy_nos:
                policy_nos.append(policy_no)
        if len(policy_nos) > DETAIL_LIMIT:
            notes.append(f"한 번에 {DETAIL_LIMIT}개까지라 앞의 {DETAIL_LIMIT}개만 봄")
            policy_nos = policy_nos[:DETAIL_LIMIT]
        record.policy_nos = policy_nos

        repository = self._deps.policy_repository
        details = await asyncio.gather(*(repository.get_detail(policy_no) for policy_no in policy_nos))
        regions = await repository.get_region_labels(policy_nos)
        blocks = [
            self._detail_block(detail, regions.get(detail.record.policy_no))
            for detail in details
            if detail is not None and detail.is_active
        ]
        return "\n\n".join([*blocks, *notes]) or "조회한 정책 없음"

    def _brief_line(self, brief: PolicyBriefRecord, region: str | None, category: Category | None) -> str:
        record = brief.record
        found = category_of(record.large_categories)
        middles = _middles(record.large_categories, brief.middle_categories, category)
        kind = " / ".join(part for part in (_CATEGORY_LABELS.get(found) if found else None, ", ".join(middles)) if part)
        return " | ".join([
            self._seen.label(record.policy_no),
            record.policy_name,
            kind or "분야 정보 없음",
            self._status(record.application_period_type_code, record.application_start_date,
                         record.application_end_date),
            region or "지역 정보 없음",
            _summary(record.policy_name, brief.description, brief.support_content),
        ])

    def _detail_block(self, detail: PolicyDetailRecord, region: str | None) -> str:
        record = detail.record
        start, end = record.application_start_date, record.application_end_date
        period = f"{start or ''} ~ {end or ''}" if start or end else (detail.application_period_raw or "정보 없음")
        fields = [
            ("상태", self._status(record.application_period_type_code, start, end)),
            ("신청 기간", period),
            ("지원 내용", detail.support_content),
            ("추가 자격", detail.additional_qualification),
            ("참여 제한", detail.participation_exclusion),
            ("신청 방법", detail.application_method),
            ("주관 기관", record.supervising_org_name),
        ]
        head = f"{self._seen.label(record.policy_no)} | {record.policy_name} | {region or '지역 정보 없음'}"
        return "\n".join([head, *(f"- {name}: {_clip(value, DETAIL_FIELD_CHARS)}" for name, value in fields)])

    def _status(self, period_type_code: str | None, start: date | None, end: date | None) -> str:
        status = application_status(period_type_code, start, end, self._today)
        label = STATUS_LABELS[status]
        if status == ApplicationStatus.UPCOMING and start:
            return f"{label}, {start} 시작"
        if status in (ApplicationStatus.OPEN, ApplicationStatus.UPCOMING) and end:
            return f"{label}, {end} 마감"
        return label


# 진행 문구 (SSE progress). 도구 인자로 만들어 LLM이 따로 쓰지 않음
def progress_text(call: ToolCall) -> str:
    if call.name == DETAIL_TOOL["name"]:
        return "정책 내용 확인 중…"
    category = call.arguments.get("category")
    return f"{category} 정책 찾는 중…" if category in AGENT_CATEGORIES else "정책 찾는 중…"


def _sub_categories(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [OLD_MIDDLE_CATEGORY_NAMES.get(name, name) for name in (str(v).strip() for v in value) if name]


def _sub_key(name: str) -> str:
    return "".join(name.replace("·", "").replace("및", "").split())


# 옛 이름은 새 이름에 더해 많은 순으로
def _merge_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    merged: dict[str, int] = {}
    for name, count in counts.items():
        key = OLD_MIDDLE_CATEGORY_NAMES.get(name, name)
        merged[key] = merged.get(key, 0) + count
    return sorted(merged.items(), key=lambda item: (-item[1], item[0]))


# 기관·지역별로 같은 이름의 공고가 따로 있어(예: "항공분야 취업지원" 3개) 점수 높은 하나만 남김
def _pick(groups, briefs: dict[str, PolicyBriefRecord], regions: dict[str, str], local_only: bool):
    picked: list[PolicyBriefRecord] = []
    names: set[str] = set()
    for group in groups:
        brief = briefs.get(group.policy_no)
        if brief is None:
            continue
        name = "".join(brief.record.policy_name.split())
        if name in names or (local_only and regions.get(group.policy_no) == "전국"):
            continue
        names.add(name)
        picked.append(brief)
        if len(picked) == SEARCH_CANDIDATES:
            break
    return picked


# 대분류·세부 분류 배열은 같은 자리끼리 짝. 찾은 분야가 있으면 그 분야의 세부 분류만 보여 줌
def _middles(large: list[str], middle: list[str], category: Category | None) -> list[str]:
    allowed = CATEGORY_NAMES[category] if category else None
    names: list[str] = []
    for large_name, middle_name in zip(large, middle, strict=False):
        name = OLD_MIDDLE_CATEGORY_NAMES.get(middle_name, middle_name)
        if (allowed is None or large_name in allowed) and name not in names:
            names.append(name)
    return names


def _summary(name: str, description: str | None, support_content: str | None) -> str:
    text = _flatten(description)
    if len(text) < MIN_DESCRIPTION_CHARS or text == name.strip():
        text = _flatten(support_content) or text
    return _clip(text, SUMMARY_CHARS)


def _flatten(value: str | None) -> str:
    return _BULLETS.sub(" ", value or "").strip()


def _clip(value: str | None, limit: int) -> str:
    text = " ".join((value or "").split())
    if not text:
        return "정보 없음"
    return text if len(text) <= limit else text[:limit].rstrip() + "…"
