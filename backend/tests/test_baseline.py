import unittest
from datetime import timedelta
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app, auth_service
from app.services.chunk_service import clean_text, split_into_chunks


class BaselineApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.principals = {}
        cls.tokens = {}
        for role in ("viewer", "operator", "admin"):
            username = f"baseline-{role}"
            cls.principals[role] = auth_service.create_user(
                username, "Baseline-pass-123!", role
            )
            response = cls.client.post(
                "/auth/token",
                data={"username": username, "password": "Baseline-pass-123!"},
            )
            if response.headers["Cache-Control"] != "no-store":
                raise AssertionError("token response must disable caching")
            cls.tokens[role] = response.json()["access_token"]

    @classmethod
    def headers(cls, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {cls.tokens[role]}"}

    def setUp(self) -> None:
        for document in self.client.get(
            "/documents", headers=self.headers("admin")
        ).json():
            self.client.delete(
                f"/documents/{document['document_id']}",
                headers=self.headers("admin"),
            )

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "mode": "mock"})
        UUID(response.headers["X-Request-Id"])

    def test_ready(self) -> None:
        response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ready", "mode": "mock", "database": "not_required"},
        )

    def test_known_question_returns_sources(self) -> None:
        loaded = self.client.post(
            "/documents/load-samples", headers=self.headers("admin")
        )
        response = self.client.post(
            "/qa/ask",
            json={"question": "差旅报销需要准备哪些材料？", "top_k": 3},
            headers=self.headers("viewer"),
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
        self.client.post(
            "/documents/load-samples", headers=self.headers("admin")
        )
        response = self.client.post(
            "/qa/ask",
            json={"question": "火星基地什么时候开放？", "top_k": 3},
            headers=self.headers("viewer"),
        )
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["is_refused"])
        self.assertEqual(body["sources"], [])

    def test_request_validation_rejects_invalid_top_k(self) -> None:
        response = self.client.post(
            "/qa/ask",
            json={"question": "测试", "top_k": 11},
            headers=self.headers("viewer"),
        )

        self.assertEqual(response.status_code, 422)

    def test_authentication_and_roles(self) -> None:
        self.assertEqual(self.client.get("/documents").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/documents/load-samples", headers=self.headers("viewer")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/auth/token",
                data={"username": "baseline-viewer", "password": "wrong"},
            ).status_code,
            401,
        )

    def test_invalid_expired_and_forged_tokens_return_401(self) -> None:
        expired = auth_service.create_access_token(
            self.principals["viewer"], timedelta(seconds=-1)
        )
        forged = f"{self.tokens['viewer']}x"
        for token in (expired, forged):
            response = self.client.get(
                "/documents", headers={"Authorization": f"Bearer {token}"}
            )
            self.assertEqual(response.status_code, 401)

    def test_request_log_does_not_include_token(self) -> None:
        token = self.tokens["viewer"]
        with self.assertLogs("uvicorn.error", level="INFO") as captured:
            response = self.client.get(
                "/documents", headers={"Authorization": f"Bearer {token}"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(token, "\n".join(captured.output))

    def test_agent_requires_operator_and_real_mode(self) -> None:
        payload = {"thread_id": "test-thread", "message": "查询订单"}

        self.assertEqual(self.client.post("/agent/run", json=payload).status_code, 401)
        self.assertEqual(
            self.client.post(
                "/agent/run", json=payload, headers=self.headers("viewer")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/agent/run", json=payload, headers=self.headers("operator")
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
