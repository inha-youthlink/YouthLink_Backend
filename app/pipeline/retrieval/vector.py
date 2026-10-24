from collections import OrderedDict

from app.llm.client import LLMClient
from app.repositories.vector_repository import VectorRepository
from app.schemas.pipeline import Chunk


# 지역·전국 정책을 나눠 같은 검색어로 두 번 검색할 때 임베딩을 한 번만 만들려고 최근 검색어만 둠
EMBEDDING_CACHE_SIZE = 128


class VectorRetriever:
    def __init__(self, llm: LLMClient, repository: VectorRepository) -> None:
        self._llm = llm
        self._repository = repository
        self._embeddings: OrderedDict[str, list[float]] = OrderedDict()

    async def retrieve(self, query: str, policy_ids: list[str] | None, k: int) -> list[Chunk]:
        embedding = await self._embed(query)
        return await self._repository.search(
            embedding=embedding,
            embedding_model=self._llm.embedding_model,
            k=k,
            policy_ids=policy_ids,
        )

    async def _embed(self, query: str) -> list[float]:
        if query in self._embeddings:
            self._embeddings.move_to_end(query)
            return self._embeddings[query]
        embedding = await self._llm.embed(query)
        self._embeddings[query] = embedding
        if len(self._embeddings) > EMBEDDING_CACHE_SIZE:
            self._embeddings.popitem(last=False)
        return embedding
