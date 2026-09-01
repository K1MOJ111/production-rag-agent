import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.chunk_service import clean_text, split_into_chunks


class BaselineApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def setUp(self) -> None:
        for document in self.client.get("/documents").json():
            self.client.delete(f"/documents/{document['document_id']}")

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "mode": "mock"})

    def test_known_question_returns_sources(self) -> None:
        loaded = self.client.post("/documents/load-samples")
        response = self.client.post(
            "/qa/ask",
            json={"question": "差旅报销需要准备哪些材料？", "top_k": 3},
        )
        body = response.json()

        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["loaded_count"], 3)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["is_refused"])
        self.assertTrue(body["sources"])
        self.assertEqual(body["sources"][0]["citation_id"], 1)
        self.assertIn("员工报销制度.txt", {item["filename"] for item in body["sources"]})

    def test_unknown_question_is_refused(self) -> None:
        self.client.post("/documents/load-samples")
        response = self.client.post(
            "/qa/ask",
            json={"question": "火星基地什么时候开放？", "top_k": 3},
        )
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["is_refused"])
        self.assertEqual(body["sources"], [])

    def test_request_validation_rejects_invalid_top_k(self) -> None:
        response = self.client.post(
            "/qa/ask",
            json={"question": "测试", "top_k": 11},
        )

        self.assertEqual(response.status_code, 422)

    def test_agent_requires_actor_header_and_real_mode(self) -> None:
        payload = {"thread_id": "test-thread", "message": "查询订单"}

        self.assertEqual(self.client.post("/agent/run", json=payload).status_code, 422)
        self.assertEqual(
            self.client.post(
                "/agent/run", json=payload, headers={"X-Actor-Id": "actor-a"}
            ).status_code,
            503,
        )


class ChunkServiceTest(unittest.TestCase):
    def test_clean_and_split_with_overlap(self) -> None:
        self.assertEqual(clean_text(" 甲\r\n\r\n 乙 "), "甲\n乙")
        self.assertEqual(
            split_into_chunks("abcdefghij", chunk_size=6, overlap=2),
            ["abcdef", "efghij"],
        )

        with self.assertRaises(ValueError):
            split_into_chunks("abc", chunk_size=3, overlap=3)


if __name__ == "__main__":
    unittest.main()
