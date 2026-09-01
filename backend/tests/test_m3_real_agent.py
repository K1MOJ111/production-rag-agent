import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


@unittest.skipUnless(
    os.getenv("RUN_REAL_INTEGRATION") == "1",
    "set RUN_REAL_INTEGRATION=1 to use real model APIs",
)
class M3RealAgentTest(unittest.TestCase):
    def test_tools_and_confirmation_gate(self) -> None:
        with TestClient(app) as client:
            before = {item["document_id"] for item in client.get("/documents").json()}
            self.assertEqual(client.post("/documents/load-samples").status_code, 200)
            try:
                cases = [
                    ("请从知识库查询差旅报销需要什么材料？", "knowledge_search"),
                    ("查询演示订单 ORD-1001 的状态。", "get_order"),
                    ("查询演示库存 SKU-A100。", "get_inventory"),
                ]
                for message, expected_tool in cases:
                    response = client.post(
                        "/agent/run",
                        json={"thread_id": uuid4().hex, "message": message},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "completed")
                    self.assertIn(expected_tool, response.json()["used_tools"])

                thread_id = uuid4().hex
                pending = client.post(
                    "/agent/run",
                    json={
                        "thread_id": thread_id,
                        "message": "请生成取消演示订单 ORD-1002 的草稿，原因是用户不再需要。",
                    },
                )
                self.assertEqual(pending.status_code, 200, pending.text)
                self.assertEqual(pending.json()["status"], "needs_confirmation")
                self.assertFalse(pending.json()["pending_action"]["draft"]["executed"])

                confirmed = client.post(
                    "/agent/confirm",
                    json={"thread_id": thread_id, "approved": True},
                )
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertEqual(confirmed.json()["status"], "completed")
                self.assertIn(
                    "draft_order_cancellation", confirmed.json()["used_tools"]
                )
            finally:
                for item in client.get("/documents").json():
                    if item["document_id"] not in before:
                        client.delete(f"/documents/{item['document_id']}")
