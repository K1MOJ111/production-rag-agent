import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    rag_mode: str
    database_url: str | None
    dashscope_api_key: str | None
    dashscope_base_url: str | None
    embedding_model: str
    embedding_dimension: int
    rerank_model: str
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    min_similarity_score: float

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if values is None else values
        mode = env.get("RAG_MODE", "mock").strip().lower()
        if mode not in {"mock", "real"}:
            raise ValueError("RAG_MODE must be 'mock' or 'real'")

        dimension = int(env.get("EMBEDDING_DIMENSION", "1024") or "1024")
        min_score = float(
            env.get("MIN_SIMILARITY_SCORE", "")
            or ("0.1" if mode == "mock" else "0.55")
        )
        if dimension <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        if not 0 <= min_score <= 1:
            raise ValueError("MIN_SIMILARITY_SCORE must be between 0 and 1")

        required = (
            "DATABASE_URL",
            "DASHSCOPE_API_KEY",
            "DASHSCOPE_BASE_URL",
            "DEEPSEEK_API_KEY",
        )
        if mode == "real":
            missing = [name for name in required if not env.get(name, "").strip()]
            if missing:
                raise ValueError(f"real mode requires: {', '.join(missing)}")

        return cls(
            rag_mode=mode,
            database_url=env.get("DATABASE_URL") or None,
            dashscope_api_key=env.get("DASHSCOPE_API_KEY") or None,
            dashscope_base_url=env.get("DASHSCOPE_BASE_URL") or None,
            embedding_model=env.get(
                "EMBEDDING_MODEL", "qwen3.7-text-embedding-flash"
            ),
            embedding_dimension=dimension,
            rerank_model=env.get("RERANK_MODEL", "qwen3-rerank"),
            deepseek_api_key=env.get("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=env.get(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ),
            deepseek_model=env.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            min_similarity_score=min_score,
        )
