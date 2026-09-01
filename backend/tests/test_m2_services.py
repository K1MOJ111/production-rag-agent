import json
import unittest

from app.services.dashscope_rerank_service import DashScopeRerankService
from app.services.prompt_service import has_valid_citations


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def read(self) -> bytes:
        return json.dumps(
            {"results": [{"index": 1, "relevance_score": 0.91}]}
        ).encode()


class M2ServiceTest(unittest.TestCase):
    def test_reranker_returns_model_order_and_score(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return FakeResponse()

        service = DashScopeRerankService(
            "unused",
            "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "qwen3-rerank",
            opener,
        )
        candidates = [
            {"chunk_id": "a", "content": "无关"},
            {"chunk_id": "b", "content": "相关"},
        ]

        results = service.rerank("问题", candidates, 1)

        self.assertEqual(results, [{"chunk_id": "b", "content": "相关", "score": 0.91}])
        self.assertTrue(calls[0][0].full_url.endswith("/compatible-api/v1/reranks"))
        self.assertEqual(calls[0][1], 30)

    def test_citations_must_exist_and_reference_only_given_sources(self) -> None:
        sources = [{"citation_id": 1}, {"citation_id": 2}]

        self.assertTrue(has_valid_citations("结论。[资料 1]", sources))
        self.assertFalse(has_valid_citations("没有引用", sources))
        self.assertFalse(has_valid_citations("错误引用。[资料 3]", sources))
