import os
import time

from app.services.guide_store import GuideStore

TTL = 600


def _expire(directory, token: str) -> None:
    old = time.time() - TTL - 1
    os.utime(directory / f"{token}.pdf", (old, old))


async def test_save_and_load(tmp_path):
    store = GuideStore(tmp_path, TTL)

    saved = await store.save(b"%PDF-1.7 guide", "청년 월세 지원 신청 가이드.pdf")
    loaded = await store.load(saved.token)

    assert len(saved.token) == 43
    assert abs(saved.expires_at.timestamp() - (time.time() + TTL)) < 5
    assert loaded is not None
    assert (loaded.content, loaded.filename) == (b"%PDF-1.7 guide", "청년 월세 지원 신청 가이드.pdf")


async def test_expired_guide_is_removed(tmp_path):
    store = GuideStore(tmp_path, TTL)
    saved = await store.save(b"pdf", "a.pdf")
    _expire(tmp_path, saved.token)

    assert await store.load(saved.token) is None
    assert list(tmp_path.iterdir()) == []


async def test_unknown_or_malformed_token(tmp_path):
    store = GuideStore(tmp_path, TTL)
    (tmp_path.parent / "secret.pdf").write_bytes(b"outside")

    assert await store.load("A" * 43) is None
    # 토큰 모양이 아니면 파일을 찾지 않음 (경로 조작)
    assert await store.load("../secret") is None
    assert await store.load("short") is None


# 새로 만들 때 만료된 파일만 지움
async def test_save_removes_expired_guides(tmp_path):
    store = GuideStore(tmp_path, TTL)
    old = await store.save(b"old", "old.pdf")
    kept = await store.save(b"kept", "kept.pdf")
    _expire(tmp_path, old.token)

    new = await store.save(b"new", "new.pdf")

    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        f"{token}{suffix}" for token in (kept.token, new.token) for suffix in (".pdf", ".name")
    )


async def test_creates_missing_directory(tmp_path):
    store = GuideStore(tmp_path / "guides", TTL)

    saved = await store.save(b"pdf", "a.pdf")

    assert (tmp_path / "guides" / f"{saved.token}.pdf").exists()
