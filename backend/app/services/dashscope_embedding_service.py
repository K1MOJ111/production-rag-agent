from langchain_core.embeddings import Embeddings
from openai import OpenAI


class DashScopeEmbeddingService(Embeddings):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.dimension = dimension
        self.client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=30,
            max_retries=2,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 20):
            batch = texts[start : start + 20]
            response = self.client.embeddings.create(
                model=self.model,
                input=batch,
                dimensions=self.dimension,
            )
            for item in sorted(response.data, key=lambda value: value.index):
                vector = list(item.embedding)
                if len(vector) != self.dimension:
                    raise RuntimeError(
                        f"embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
                    )
                vectors.append(vector)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
