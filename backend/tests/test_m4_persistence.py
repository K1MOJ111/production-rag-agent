import os
import unittest
from types import SimpleNamespace
from uuid import uuid4

from app.services.agent_service import LangGraphAgentService
from tests.test_m3_agent import FakeCompletions


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 to test durable Agent state",
)
class M4PersistenceTest(unittest.TestCase):
    def test_pending_action_survives_restart_and_keeps_owner(self) -> None:
        database_url = os.environ["DATABASE_URL"]
        thread_id = f"m4-{uuid4().hex}"
        actor_id = "m4-owner"
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        first = LangGraphAgentService(
            "fake-model", lambda question: {"question": question}, client, database_url
        )
        pending = first.run(actor_id, thread_id, "取消订单 ORD-1002")
        self.assertEqual(pending["status"], "needs_confirmation")
        first.close()

        reopened = LangGraphAgentService(
            "fake-model", lambda question: {"question": question}, client, database_url
        )
        try:
            self.assertEqual(len(reopened.list_audit(actor_id, thread_id)), 1)
            with self.assertRaises(PermissionError):
                reopened.confirm("another-actor", thread_id, approved=True)

            completed = reopened.confirm(actor_id, thread_id, approved=True)

            self.assertEqual(completed["status"], "completed")
            self.assertIn('"executed": false', completed["answer"])
            self.assertEqual(
                [item["event_type"] for item in reopened.list_audit(actor_id, thread_id)],
                ["run", "confirm"],
            )
        finally:
            reopened._checkpointer.delete_thread(thread_id)
            with reopened._audit_engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM agent_threads WHERE thread_id = %s", (thread_id,)
                )
            reopened.close()
