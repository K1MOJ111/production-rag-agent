import json
import unittest
from types import SimpleNamespace

from app.services.agent_service import DEMO_ORDERS, LangGraphAgentService


class FakeCompletions:
    def create(self, **kwargs):
        last = kwargs["messages"][-1]
        if last["role"] == "user":
            if "取消" in last["content"]:
                name = "draft_order_cancellation"
                order_id = "ORD-9999" if "不存在" in last["content"] else "ORD-1002"
                arguments = {"order_id": order_id, "reason": "用户不再需要"}
            elif "库存" in last["content"]:
                name = "get_inventory"
                arguments = {"sku": "SKU-A100"}
            else:
                name = "get_order"
                arguments = {"order_id": "ORD-1001"}
            call = SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(
                    name=name, arguments=json.dumps(arguments, ensure_ascii=False)
                ),
            )
            message = SimpleNamespace(content="", tool_calls=[call])
        else:
            message = SimpleNamespace(
                content=f"工具结果：{last['content']}", tool_calls=[]
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class LangGraphAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        self.service = LangGraphAgentService(
            "fake-model", lambda question: {"question": question}, client
        )
        self.actor_id = "actor-a"

    def test_read_only_order_tool_completes(self) -> None:
        result = self.service.run(self.actor_id, "read-thread", "查询订单 ORD-1001")

        self.assertEqual(result["status"], "completed")
        self.assertIn("已发货", result["answer"])
        self.assertEqual(result["used_tools"], ["get_order"])

    def test_sensitive_action_pauses_and_never_executes(self) -> None:
        before = dict(DEMO_ORDERS["ORD-1002"])
        pending = self.service.run(
            self.actor_id, "confirm-thread", "取消订单 ORD-1002"
        )

        self.assertEqual(pending["status"], "needs_confirmation")
        self.assertFalse(pending["pending_action"]["draft"]["executed"])
        with self.assertRaisesRegex(ValueError, "waiting for confirmation"):
            self.service.run(self.actor_id, "confirm-thread", "继续")

        completed = self.service.confirm(
            self.actor_id, "confirm-thread", approved=True
        )

        self.assertEqual(completed["status"], "completed")
        self.assertIn('"executed": false', completed["answer"])
        self.assertEqual(completed["used_tools"], ["draft_order_cancellation"])
        self.assertEqual(DEMO_ORDERS["ORD-1002"], before)

    def test_inventory_tool_is_read_only(self) -> None:
        result = self.service.run(
            self.actor_id, "inventory-thread", "查询 SKU-A100 库存"
        )

        self.assertEqual(result["used_tools"], ["get_inventory"])
        self.assertIn('"available": 18', result["answer"])

    def test_sensitive_action_can_be_rejected(self) -> None:
        self.service.run(self.actor_id, "reject-thread", "取消订单 ORD-1002")

        completed = self.service.confirm(
            self.actor_id, "reject-thread", approved=False
        )

        self.assertIn('"approved": false', completed["answer"])
        self.assertEqual(completed["used_tools"], ["draft_order_cancellation"])

    def test_missing_order_does_not_request_confirmation(self) -> None:
        result = self.service.run(
            self.actor_id, "missing-thread", "取消不存在的订单"
        )

        self.assertEqual(result["status"], "completed")
        self.assertIn("未生成草稿", result["answer"])

    def test_thread_owner_and_audit_are_enforced(self) -> None:
        self.service.run(self.actor_id, "owned-thread", "查询订单 ORD-1001")

        with self.assertRaises(PermissionError):
            self.service.run("actor-b", "owned-thread", "查询订单 ORD-1001")
        entries = self.service.list_audit(self.actor_id, "owned-thread")
        self.assertEqual(entries[0]["event_type"], "run")
        self.assertEqual(entries[0]["used_tools"], ["get_order"])
