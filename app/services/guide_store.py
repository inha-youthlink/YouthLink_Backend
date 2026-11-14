import asyncio
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# 신청 가이드 PDF 보관 (계약 4.7). 링크를 연 브라우저는 X-Client-Id를 보낼 수 없어 추측할 수 없는 토큰이 열쇠 역할을 함
# 서버 워커가 여러 개여도 같은 서버 안에서는 링크가 열리도록 메모리 대신 파일로 둠
# 만료는 파일을 쓴 시각 기준이라 서명 키나 따로 적어 둔 만료 시각이 필요 없음

# secrets.token_urlsafe(32)는 43자. 이 모양이 아니면 파일을 찾지 않아 경로 조작을 막음
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_PDF_SUFFIX = ".pdf"
# 내려받을 때 쓸 파일 이름(정책 이름)은 PDF 옆에 따로 둠
_NAME_SUFFIX = ".name"


@dataclass(frozen=True)
class SavedGuide:
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredGuide:
    content: bytes
    filename: str


class GuideStore:
    def __init__(self, directory: Path, ttl_seconds: int) -> None:
        self._directory = directory
        self._ttl_seconds = ttl_seconds

    async def save(self, content: bytes, filename: str) -> SavedGuide:
        return await asyncio.to_thread(self._save, content, filename)

    # 만료되었거나 없으면 None
    async def load(self, token: str) -> StoredGuide | None:
        return await asyncio.to_thread(self._load, token)

    def _save(self, content: bytes, filename: str) -> SavedGuide:
        self._directory.mkdir(parents=True, exist_ok=True)
        # 정리 작업을 따로 돌리지 않고 새로 만들 때 만료된 파일을 지움
        self._remove_expired()
        token = secrets.token_urlsafe(32)
        self._path(token, _NAME_SUFFIX).write_text(filename, encoding="utf-8")
        pdf_path = self._path(token, _PDF_SUFFIX)
        pdf_path.write_bytes(content)
        expires_at = pdf_path.stat().st_mtime + self._ttl_seconds
        return SavedGuide(token=token, expires_at=datetime.fromtimestamp(expires_at, UTC))

    def _load(self, token: str) -> StoredGuide | None:
        if not _TOKEN_PATTERN.fullmatch(token):
            return None
        pdf_path = self._path(token, _PDF_SUFFIX)
        try:
            if self._expired(pdf_path):
                self._remove(token)
                return None
            content = pdf_path.read_bytes()
            filename = self._path(token, _NAME_SUFFIX).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return StoredGuide(content=content, filename=filename)

    def _remove_expired(self) -> None:
        for pdf_path in self._directory.glob(f"*{_PDF_SUFFIX}"):
            try:
                if self._expired(pdf_path):
                    self._remove(pdf_path.stem)
            except FileNotFoundError:
                # 다른 워커가 먼저 지운 경우
                continue

    def _expired(self, pdf_path: Path) -> bool:
        return time.time() >= pdf_path.stat().st_mtime + self._ttl_seconds

    def _remove(self, token: str) -> None:
        for suffix in (_PDF_SUFFIX, _NAME_SUFFIX):
            self._path(token, suffix).unlink(missing_ok=True)

    def _path(self, token: str, suffix: str) -> Path:
        return self._directory / f"{token}{suffix}"
