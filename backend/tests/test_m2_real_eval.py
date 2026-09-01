import asyncio
import json
import os
import unittest
from pathlib import Path

from app.config import Settings
from app.services.dashscope_embedding_service import DashScopeEmbeddingService
from app.services.dashscope_rerank_service import DashScopeRerankService
from app.services.document_service import DocumentService
from app.services.postgres_vector_store import PostgresVectorStore


@unittest.skipUnless(
    os.getenv("RUN_REAL_INTEGRATION") == "1",
    "set RUN_REAL_INTEGRATION=1 to use PostgreSQL and paid model APIs",
)
class M2RealEvalTest(unittest.TestCase):
    def test_fixed_retrieval_cases(self) -> None:
        settings = Settings.from_env()
        embedder = DashScopeEmbeddingService(
            settings.dashscope_api_key,
            settings.dashscope_base_url,
            settings.embedding_model,
            settings.embedding_dimension,
        )
        store = PostgresVectorStore(
            settings.database_url, embedder, settings.embedding_dimension
        )
        reranker = DashScopeRerankService(
            settings.dashscope_api_key,
            settings.dashscope_base_url,
            settings.rerank_model,
        )
        root = Path(__file__).resolve().parents[2]
        existing_ids = {item["document_id"] for item in store.list_documents()}
        loaded = DocumentService(store, embedder).load_sample_documents(
            root / "sample_docs"
        )
        created_ids = [
            item["document_id"]
            for item in loaded
            if item["document_id"] not in existing_ids
        ]
        cases = json.loads(
            (root / "backend" / "evals" / "m2_cases.json").read_text(encoding="utf-8")
        )

        try:
            for case in cases:
                candidates = store.search(case["question"], 12, embedder)
                results = reranker.rerank(case["question"], candidates, 3)
                if case["expected_filename"]:
                    self.assertTrue(results, case["question"])
                    self.assertEqual(
                        results[0]["filename"],
                        case["expected_filename"],
                        case["question"],
                    )
                    self.assertGreaterEqual(
                        results[0]["score"], settings.min_similarity_score
                    )
                else:
                    self.assertTrue(
                        not results
                        or results[0]["score"] < settings.min_similarity_score,
                        case["question"],
                    )
        finally:
            for document_id in created_ids:
                store.delete_document(document_id)
            asyncio.run(store.close())
