import json
import os
import unittest
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import text

from app.services.agent_service import LangGraphAgentService
from app.services.business_adapter import PostgresBusinessAdapter


class BusinessCompletions:
    def __init__(self, order_id: str, sku: str) -> None:
        self.order_id = order_id
        self.sku = sku

    def create(self, **kwargs):
        last = kwargs["messages"][-1]
        if last["role"] == "user":
            if "取消" in last["content"]:
                name = "draft_order_cancellation"
                arguments = {"order_id": self.order_id, "reason": "重复下单"}
            elif "库存" in last["content"]:
                name = "get_inventory"
                arguments = {"sku": self.sku}
            else:
                name = "get_order"
                arguments = {"order_id": self.order_id}
            message = SimpleNamespace(
                content="",
                tool_calls=[
                    SimpleNamespace(
                        id="call-m8",
                        function=SimpleNamespace(
                            name=name,
                            arguments=json.dumps(arguments, ensure_ascii=False),
                        ),
                    )
                ],
            )
        else:
            message = SimpleNamespace(
                content=f"工具结果：{last['content']}", tool_calls=[]
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 to test M8 business transactions",
)
class M8BusinessIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = os.environ["DATABASE_URL"]
        suffix = uuid4().hex[:10]
        self.sku = f"SKU-M8-{suffix}"
        self.order_id = f"ORD-M8-{suffix}"
        self.reject_order_id = f"ORD-M8R-{suffix}"
        self.actor_id = f"m8-owner-{suffix}"
        self.completions = BusinessCompletions(self.order_id, self.sku)
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.completions)
        )
        self.threads: list[str] = []
        adapter = PostgresBusinessAdapter(self.database_url)
        with adapter._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO inventory (sku, available, warehouse)
                    VALUES (:sku, 7, 'M8测试仓')
                    """
                ),
                {"sku": self.sku},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO orders (order_id, status, sku, quantity)
                    VALUES
                        (:order_id, '待处理', :sku, 2),
                        (:reject_order_id, '待处理', :sku, 1)
                    """
                ),
                {
                    "order_id": self.order_id,
                    "reject_order_id": self.reject_order_id,
                    "sku": self.sku,
                },
            )
        self.service = self._new_service(adapter)

    def _new_service(self, adapter=None) -> LangGraphAgentService:
        return LangGraphAgentService(
            "fake-model",
            lambda question: {"question": question},
            self.client,
            adapter or PostgresBusinessAdapter(self.database_url),
            self.database_url,
        )

    def tearDown(self) -> None:
        for thread_id in self.threads:
            self.service._checkpointer.delete_thread(thread_id)
        with self.service._audit_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM cancellation_requests "
                    "WHERE order_id IN (:order_id, :reject_order_id)"
                ),
                {
                    "order_id": self.order_id,
                    "reject_order_id": self.reject_order_id,
                },
            )
            for thread_id in self.threads:
                connection.execute(
                    text("DELETE FROM agent_threads WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                )
            connection.execute(
                text(
                    "DELETE FROM orders "
                    "WHERE order_id IN (:order_id, :reject_order_id)"
                ),
                {
                    "order_id": self.order_id,
                    "reject_order_id": self.reject_order_id,
                },
            )
            connection.execute(
                text("DELETE FROM inventory WHERE sku = :sku"), {"sku": self.sku}
            )
        self.service.close()

    def _thread(self, label: str) -> str:
        thread_id = f"m8-{label}-{uuid4().hex}"
        self.threads.append(thread_id)
        return thread_id

    def test_order_and_inventory_tools_read_postgres(self) -> None:
        order_thread = self._thread("order")
        order = self.service.run(
            self.actor_id, order_thread, "查询订单", "request-order"
        )
        inventory_thread = self._thread("inventory")
        inventory = self.service.run(
            self.actor_id, inventory_thread, "查询库存", "request-inventory"
        )

        self.assertEqual(order["used_tools"], ["get_order"])
        self.assertIn(self.order_id, order["answer"])
        self.assertIn('"quantity": 2', order["answer"])
        self.assertEqual(inventory["used_tools"], ["get_inventory"])
        self.assertIn('"available": 7', inventory["answer"])
        audit = self.service.list_audit(self.actor_id, order_thread)[0]
        self.assertEqual(audit["details"]["request_id"], "request-order")

    def test_approval_rejection_idempotency_owner_and_restart(self) -> None:
        approve_thread = self._thread("approve")
        pending = self.service.run(
            self.actor_id, approve_thread, "取消订单", "request-draft"
        )
        self.assertEqual(pending["status"], "needs_confirmation")
        self.assertFalse(pending["pending_action"]["draft"]["executed"])
        self.assertEqual(self.service.business.get_order(self.order_id)["status"], "待处理")
        with self.service.business._engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM cancellation_requests "
                        "WHERE order_id = :order_id"
                    ),
                    {"order_id": self.order_id},
                ).scalar_one(),
                0,
            )

        self.service.close()
        self.service = self._new_service()
        with self.assertRaises(PermissionError):
            self.service.confirm(
                "m8-admin-other-user", approve_thread, True, "request-forbidden"
            )

        approved = self.service.confirm(
            self.actor_id, approve_thread, True, "request-approve"
        )
        self.assertIn('"executed": true', approved["answer"])
        self.assertEqual(self.service.business.get_order(self.order_id)["status"], "已取消")
        with self.assertRaisesRegex(ValueError, "not waiting"):
            self.service.confirm(
                self.actor_id, approve_thread, True, "request-duplicate"
            )

        draft = pending["pending_action"]["draft"]
        idempotent = self.service.business.approve_cancellation(
            self.actor_id, approve_thread, draft, "request-retry"
        )
        self.assertTrue(idempotent["idempotent"])
        with self.service.business._engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM cancellation_requests "
                    "WHERE order_id = :order_id"
                ),
                {"order_id": self.order_id},
            ).scalar_one()
        self.assertEqual(count, 1)

        audit = self.service.list_audit(self.actor_id, approve_thread)
        self.assertEqual([item["event_type"] for item in audit], ["run", "confirm"])
        self.assertEqual(audit[0]["details"]["request_id"], "request-draft")
        self.assertEqual(audit[1]["actor_id"], self.actor_id)
        self.assertEqual(audit[1]["thread_id"], approve_thread)
        self.assertEqual(audit[1]["details"]["action"], "cancel_order")
        self.assertEqual(audit[1]["details"]["result"], "executed")
        self.assertEqual(audit[1]["details"]["request_id"], "request-approve")

        self.completions.order_id = self.reject_order_id
        reject_thread = self._thread("reject")
        self.service.run(
            self.actor_id, reject_thread, "取消订单", "request-reject-draft"
        )
        rejected = self.service.confirm(
            self.actor_id, reject_thread, False, "request-reject"
        )
        self.assertIn('"executed": false', rejected["answer"])
        self.assertEqual(
            self.service.business.get_order(self.reject_order_id)["status"], "待处理"
        )
        with self.service.business._engine.connect() as connection:
            rejected_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM cancellation_requests "
                    "WHERE order_id = :order_id"
                ),
                {"order_id": self.reject_order_id},
            ).scalar_one()
        self.assertEqual(rejected_count, 0)
        rejected_audit = self.service.list_audit(self.actor_id, reject_thread)
        self.assertEqual(rejected_audit[-1]["details"]["result"], "rejected")
        self.assertEqual(rejected_audit[-1]["details"]["request_id"], "request-reject")


if __name__ == "__main__":
    unittest.main()
