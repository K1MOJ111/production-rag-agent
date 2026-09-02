import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app import main
from tests.test_m8_business import BusinessCompletions


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" and main.agent_service,
    "set RUN_POSTGRES_INTEGRATION=1 with real mode to test M8 API roles",
)
class M8ApiAuthorizationTest(unittest.TestCase):
    def test_viewer_is_blocked_and_admin_cannot_confirm_other_user(self) -> None:
        suffix = uuid4().hex
        thread_id = f"m8-api-{suffix}"
        users = [
            (f"m8-viewer-{suffix}", "viewer"),
            (f"m8-operator-{suffix}", "operator"),
            (f"m8-admin-{suffix}", "admin"),
        ]
        principals = [
            main.auth_service.create_user(username, "M8-api-pass-123!", role)
            for username, role in users
        ]
        original_client = main.agent_service.client
        main.agent_service.client = type(
            "Client",
            (),
            {
                "chat": type(
                    "Chat",
                    (),
                    {
                        "completions": BusinessCompletions(
                            "ORD-1002", "SKU-B200"
                        )
                    },
                )()
            },
        )()
        with main.agent_service.business._engine.begin() as connection:
            connection.execute(
                text("UPDATE orders SET status = '待处理' WHERE order_id = 'ORD-1002'")
            )

        client = TestClient(main.app)
        try:
            headers = []
            for username, _ in users:
                token = client.post(
                    "/auth/token",
                    data={"username": username, "password": "M8-api-pass-123!"},
                ).json()["access_token"]
                headers.append({"Authorization": f"Bearer {token}"})

            payload = {"thread_id": thread_id, "message": "取消订单 ORD-1002"}
            self.assertEqual(
                client.post("/agent/run", json=payload, headers=headers[0]).status_code,
                403,
            )
            pending = client.post("/agent/run", json=payload, headers=headers[1])
            self.assertEqual(pending.status_code, 200, pending.text)
            self.assertEqual(pending.json()["status"], "needs_confirmation")
            self.assertEqual(
                client.post(
                    "/agent/confirm",
                    json={"thread_id": thread_id, "approved": True},
                    headers=headers[2],
                ).status_code,
                403,
            )
            rejected = client.post(
                "/agent/confirm",
                json={"thread_id": thread_id, "approved": False},
                headers=headers[1],
            )
            self.assertEqual(rejected.status_code, 200, rejected.text)
        finally:
            client.close()
            main.agent_service.client = original_client
            main.agent_service._checkpointer.delete_thread(thread_id)
            with main.agent_service._audit_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM cancellation_requests WHERE thread_id = :thread_id"
                    ),
                    {"thread_id": thread_id},
                )
                connection.execute(
                    text("DELETE FROM agent_threads WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )
                connection.execute(
                    text("UPDATE orders SET status = '待处理' WHERE order_id = 'ORD-1002'")
                )
            with main.auth_service._engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM users "
                        "WHERE user_id = ANY(CAST(:user_ids AS UUID[]))"
                    ),
                    {"user_ids": [principal.user_id for principal in principals]},
                )


if __name__ == "__main__":
    unittest.main()
