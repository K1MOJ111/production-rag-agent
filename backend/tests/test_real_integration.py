import asyncio
import os
import unittest
from pathlib import Path

from app.config import Settings
from app.services.dashscope_embedding_service import DashScopeEmbeddingService
from app.services.dashscope_rerank_service import DashScopeRerankService
from app.services.deepseek_llm_service import DeepSeekLLMService
from app.services.document_service import DocumentService
from app.services.postgres_vector_store import PostgresVectorStore
from app.services.prompt_service import build_prompt, has_valid_citations


@unittest.skipUnless(
    os.getenv("RUN_REAL_INTEGRATION") == "1",
    "set RUN_REAL_INTEGRATION=1 to use PostgreSQL and paid model APIs",
)
class RealRagIntegrationTest(unittest.TestCase):
    def test_persistence_retrieval_refusal_and_answer(self) -> None:
        settings = Settings.from_env()
        self.assertEqual(settings.rag_mode, "real")
        embedder = DashScopeEmbeddingService(
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        )
        store = PostgresVectorStore(
            settings.database_url, embedder, settings.embedding_dimension
        )
        sample = Path(__file__).resolve().parents[2] / "sample_docs" / "员工报销制度.txt"
        document = DocumentService(store, embedder).add_text_document(
            sample.name, sample.read_text(encoding="utf-8")
        )
        document_id = document["document_id"]
        asyncio.run(store.close())

        reopened = PostgresVectorStore(
            settings.database_url, embedder, settings.embedding_dimension
        )
        reranker = DashScopeRerankService(
            settings.dashscope_api_key,
            settings.dashscope_base_url,
            settings.rerank_model,
        )
        try:
            self.assertIn(
                document_id,
                {item["document_id"] for item in reopened.list_documents()},
            )
            candidates = reopened.search("差旅报销需要哪些材料？", 12, embedder)
            sources = reranker.rerank("差旅报销需要哪些材料？", candidates, 3)
            self.assertTrue(sources)
            self.assertGreaterEqual(sources[0]["score"], settings.min_similarity_score)

            unrelated_candidates = reopened.search("火星基地什么时候开放？", 12, embedder)
            unrelated = reranker.rerank(
                "火星基地什么时候开放？", unrelated_candidates, 3
            )
            self.assertTrue(
                not unrelated
                or unrelated[0]["score"] < settings.min_similarity_score
            )

            cited_sources = [
                {**source, "citation_id": index}
                for index, source in enumerate(sources, start=1)
            ]
            answer = DeepSeekLLMService(
                settings.deepseek_api_key,
                settings.deepseek_base_url,
                settings.deepseek_model,
            ).generate_answer(
                "差旅报销需要哪些材料？",
                cited_sources,
                build_prompt("差旅报销需要哪些材料？", cited_sources),
            )
            self.assertTrue(has_valid_citations(answer, cited_sources))
        finally:
            reopened.delete_document(document_id)
            asyncio.run(reopened.close())
