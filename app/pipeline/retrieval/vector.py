from app.llm.client import LLMClient
from app.repositories.vector_repository import VectorRepository
from app.schemas.pipeline import Chunk


class VectorRetriever:
    def __init__(self, llm: LLMClient, repository: VectorRepository) -> None:
        self._llm = llm
        self._repository = repository

    async def retrieve(self, query: str, policy_ids: list[str] | None, k: int) -> list[Chunk]:
        embedding = await self._llm.embed(query)
        return await self._repository.search(
            embedding=embedding,
            embedding_model=self._llm.embedding_model,
            k=k,
            policy_ids=policy_ids,
        )
