import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import agent_service, app, auth_service


@unittest.skipUnless(
    os.getenv("RUN_REAL_INTEGRATION") == "1",
    "set RUN_REAL_INTEGRATION=1 to use real model APIs",
)
class M3RealAgentTest(unittest.TestCase):
    def test_tools_and_confirmation_gate(self) -> None:
        with TestClient(app) as client:
            suffix = uuid4().hex
            created_users = [
                (f"m3-operator-{suffix}", "operator"),
                (f"m3-other-{suffix}", "operator"),
                (f"m3-admin-{suffix}", "admin"),
            ]
            principals = [
                auth_service.create_user(username, "M3-real-pass-123!", role)
                for username, role in created_users
            ]

            def headers(index: int) -> dict[str, str]:
                response = client.post(
                    "/auth/token",
                    data={
                        "username": created_users[index][0],
                        "password": "M3-real-pass-123!",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                return {
                    "Authorization": f"Bearer {response.json()['access_token']}"
                }

            operator_headers = headers(0)
            other_headers = headers(1)
            admin_headers = headers(2)
            thread_ids = []
            with agent_service.business._engine.begin() as connection:
                connection.execute(
                    text("UPDATE orders SET status = '待处理' WHERE order_id = 'ORD-1002'")
                )
            before = {
                item["document_id"]
                for item in client.get("/documents", headers=admin_headers).json()
            }
            self.assertEqual(
                client.post(
                    "/documents/load-samples", headers=admin_headers
                ).status_code,
                200,
            )
            try:
                cases = [
                    ("请从知识库查询差旅报销需要什么材料？", "knowledge_search"),
                    ("查询本地订单 ORD-1001 的状态。", "get_order"),
                    ("查询本地库存 SKU-A100。", "get_inventory"),
                ]
                for message, expected_tool in cases:
                    thread_id = uuid4().hex
                    thread_ids.append(thread_id)
                    response = client.post(
                        "/agent/run",
                        json={"thread_id": thread_id, "message": message},
                        headers=operator_headers,
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "completed")
                    self.assertIn(expected_tool, response.json()["used_tools"])

                thread_id = uuid4().hex
                thread_ids.append(thread_id)
                pending = client.post(
                    "/agent/run",
                    json={
                        "thread_id": thread_id,
                        "message": "请生成取消本地订单 ORD-1002 的草稿，原因是用户不再需要。",
                    },
                    headers=operator_headers,
                )
                self.assertEqual(pending.status_code, 200, pending.text)
                self.assertEqual(pending.json()["status"], "needs_confirmation")
                self.assertFalse(pending.json()["pending_action"]["draft"]["executed"])

                admin_confirm = client.post(
                    "/agent/confirm",
                    json={"thread_id": thread_id, "approved": True},
                    headers=admin_headers,
                )
                self.assertEqual(admin_confirm.status_code, 403)

                confirmed = client.post(
                    "/agent/confirm",
                    json={"thread_id": thread_id, "approved": True},
                    headers=operator_headers,
                )
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                self.assertEqual(confirmed.json()["status"], "completed")
                self.assertIn('"executed": true', confirmed.json()["answer"])
                self.assertIn(
                    "draft_order_cancellation", confirmed.json()["used_tools"]
                )
                audit = client.get(
                    f"/agent/{thread_id}/audit", headers=operator_headers
                )
                self.assertEqual(audit.status_code, 200, audit.text)
                self.assertEqual(
                    [item["event_type"] for item in audit.json()],
                    ["run", "confirm"],
                )
                forbidden = client.get(
                    f"/agent/{thread_id}/audit",
                    headers=other_headers,
                )
                self.assertEqual(forbidden.status_code, 403)
                self.assertEqual(
                    client.get(
                        f"/agent/{thread_id}/audit", headers=admin_headers
                    ).status_code,
                    200,
                )
            finally:
                for item in client.get(
                    "/documents", headers=admin_headers
                ).json():
                    if item["document_id"] not in before:
                        client.delete(
                            f"/documents/{item['document_id']}",
                            headers=admin_headers,
                        )
                for thread_id in thread_ids:
                    agent_service._checkpointer.delete_thread(thread_id)
                    with agent_service._audit_engine.begin() as connection:
                        connection.execute(
                            text(
                                "DELETE FROM cancellation_requests "
                                "WHERE thread_id = :thread_id"
                            ),
                            {"thread_id": thread_id},
                        )
                        connection.exec_driver_sql(
                            "DELETE FROM agent_threads WHERE thread_id = %s",
                            (thread_id,),
                        )
                with agent_service.business._engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE orders SET status = '待处理' "
                            "WHERE order_id = 'ORD-1002'"
                        )
                    )
                with auth_service._engine.begin() as connection:
                    connection.execute(
                        text(
                            "DELETE FROM users "
                            "WHERE user_id = ANY(CAST(:user_ids AS UUID[]))"
                        ),
                        {"user_ids": [principal.user_id for principal in principals]},
                    )
