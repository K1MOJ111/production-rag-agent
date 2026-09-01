import math
import re
from collections import Counter


class MockEmbeddingService:
    """Small local embedding stand-in for learning the RAG flow.

    It converts text into token-frequency vectors and compares them with cosine
    similarity. A real project can replace this class with an API-based
    embedding model without changing the rest of the flow.
    """

    def embed(self, text: str) -> dict[str, float]:
        tokens = self._tokenize(text)
        counts = Counter(tokens)
        return {token: float(count) for token, count in counts.items()}

    def similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        if not left or not right:
            return 0.0

        shared_tokens = set(left) & set(right)
        dot = sum(left[token] * right[token] for token in shared_tokens)
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))

        if left_norm == 0 or right_norm == 0:
            return 0.0

        return dot / (left_norm * right_norm)

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        english_tokens = re.findall(r"[a-z0-9]+", text)
        chinese_segments = re.findall(r"[\u4e00-\u9fff]+", text)

        chinese_tokens: list[str] = []
        for segment in chinese_segments:
            chinese_tokens.extend(segment)
            chinese_tokens.extend(
                segment[index : index + 2] for index in range(len(segment) - 1)
            )

        return english_tokens + chinese_tokens
