from dataclasses import dataclass

from app.schemas.pipeline import Chunk


@dataclass(frozen=True)
class PolicyGroup:
    policy_no: str
    score: float
    chunks: list[Chunk]


def group_by_policy(chunks: list[Chunk]) -> list[PolicyGroup]:
    by_policy: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_policy.setdefault(chunk.policy_no, []).append(chunk)

    groups = [
        PolicyGroup(
            policy_no=policy_no,
            # 평균을 쓰면 여러 청크로 나뉜 정책이 불리해져 최고 점수를 대표값으로 사용함
            score=max(c.score for c in policy_chunks),
            chunks=sorted(policy_chunks, key=lambda c: c.chunk_index),
        )
        for policy_no, policy_chunks in by_policy.items()
    ]
    return sorted(groups, key=lambda g: g.score, reverse=True)
