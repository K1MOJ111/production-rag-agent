import asyncio
import sys
import unittest
from types import SimpleNamespace

from app.config import Settings
from app.services.dashscope_embedding_service import DashScopeEmbeddingService
from app.services.deepseek_llm_service import DeepSeekLLMService
from app.services.postgres_vector_store import (
    PostgresVectorStore,
    _configure_postgres_event_loop,
)


class SettingsTest(unittest.TestCase):
    def test_mock_defaults_do_not_require_secrets(self) -> None:
        settings = Settings.from_env({})

        self.assertEqual(settings.rag_mode, "mock")
        self.assertEqual(settings.min_similarity_score, 0.1)
        self.assertEqual(
            Settings.from_env({"MIN_SIMILARITY_SCORE": ""}).min_similarity_score,
            0.1,
        )

    def test_real_mode_requires_database_and_api_settings(self) -> None:
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            Settings.from_env({"RAG_MODE": "real"})

        settings = Settings.from_env(
            {
                "RAG_MODE": "real",
                "DATABASE_URL": "postgresql+psycopg://example",
                "DASHSCOPE_API_KEY": "test-key",
                "DASHSCOPE_BASE_URL": "https://example.com/v1",
                "DEEPSEEK_API_KEY": "test-key",
            }
        )
        self.assertEqual(settings.embedding_dimension, 1024)
        self.assertEqual(settings.rerank_model, "qwen3-rerank")
        self.assertEqual(settings.min_similarity_score, 0.55)


class FakeEmbeddingsApi:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        dimensions = kwargs["dimensions"]
        data = [
            SimpleNamespace(index=index, embedding=[float(index)] * dimensions)
            for index, _ in reversed(list(enumerate(kwargs["input"])))
        ]
        return SimpleNamespace(data=data)


class DashScopeEmbeddingServiceTest(unittest.TestCase):
    def test_embeddings_use_configured_model_dimension_and_order(self) -> None:
        api = FakeEmbeddingsApi()
        client = SimpleNamespace(embeddings=api)
        service = DashScopeEmbeddingService(
            api_key="unused",
            base_url="https://example.com/v1",
            model="embedding-test",
            dimension=4,
            client=client,
        )

        vectors = service.embed_documents(["甲", "乙"])

        self.assertEqual(vectors, [[0.0] * 4, [1.0] * 4])
        self.assertEqual(api.calls[0]["model"], "embedding-test")
        self.assertEqual(api.calls[0]["dimensions"], 4)


class FakeCompletionsApi:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="制度要求提交发票。[资料 1]")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class DeepSeekLLMServiceTest(unittest.TestCase):
    def test_answer_uses_configured_model_and_grounding_prompt(self) -> None:
        completions = FakeCompletionsApi()
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        service = DeepSeekLLMService(
            api_key="unused",
            base_url="https://example.com",
            model="deepseek-test",
            client=client,
        )

        answer = service.generate_answer(
            question="如何报销？",
            sources=[{"citation_id": 1}],
            prompt="参考资料：[资料 1]",
        )

        self.assertIn("[资料 1]", answer)
        self.assertEqual(completions.calls[0]["model"], "deepseek-test")
        self.assertIn("只能依据", completions.calls[0]["messages"][0]["content"])


class DocumentHashTest(unittest.TestCase):
    def test_document_hash_is_stable_and_content_sensitive(self) -> None:
        first = PostgresVectorStore.document_hash(["甲", "乙"])

        self.assertEqual(first, PostgresVectorStore.document_hash(["甲", "乙"]))
        self.assertNotEqual(first, PostgresVectorStore.document_hash(["甲乙"]))
        self.assertEqual(len(first), 64)

    @unittest.skipUnless(sys.platform == "win32", "Windows compatibility check")
    def test_postgres_uses_selector_event_loop_on_windows(self) -> None:
        _configure_postgres_event_loop()
        loop = asyncio.new_event_loop()
        try:
            self.assertIsInstance(loop, asyncio.SelectorEventLoop)
        finally:
            loop.close()
