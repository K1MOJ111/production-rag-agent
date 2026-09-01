import json
import operator
from datetime import datetime, timezone
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from openai import OpenAI
from psycopg import Connection
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text


DEMO_ORDERS = {
    "ORD-1001": {"status": "已发货", "sku": "SKU-A100", "quantity": 2},
    "ORD-1002": {"status": "待处理", "sku": "SKU-B200", "quantity": 1},
}
DEMO_INVENTORY = {
    "SKU-A100": {"available": 18, "warehouse": "深圳仓"},
    "SKU-B200": {"available": 0, "warehouse": "深圳仓"},
}


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    used_tools: Annotated[list[str], operator.add]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "knowledge_search",
            "description": "检索企业制度知识库并返回带引用编号的资料。",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "查询演示订单，只读。",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "description": "查询演示库存，只读。",
            "parameters": {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_order_cancellation",
            "description": "生成取消订单草稿；必须人工确认，且不会实际取消订单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["order_id", "reason"],
            },
        },
    },
]


class LangGraphAgentService:
    def __init__(
        self,
        model: str,
        knowledge_search,
        client: OpenAI,
        database_url: str | None = None,
    ) -> None:
        self.model = model
        self.knowledge_search = knowledge_search
        self.client = client
        self._thread_actors: dict[str, str] = {}
        self._audit_entries: list[dict] = []
        self._checkpoint_connection = None
        self._audit_engine = None
        checkpointer = InMemorySaver()
        if database_url:
            dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
            self._checkpoint_connection = Connection.connect(
                dsn, autocommit=True, row_factory=dict_row
            )
            checkpointer = PostgresSaver(
                self._checkpoint_connection,
                serde=JsonPlusSerializer(allowed_msgpack_modules=[]),
            )
            checkpointer.setup()
            self._audit_engine = create_engine(database_url, pool_pre_ping=True)
            self._ensure_audit_schema()
        self._checkpointer = checkpointer
        graph = StateGraph(AgentState)
        graph.add_node("agent", self._call_model)
        graph.add_node("tools", self._run_tools)
        graph.add_edge(START, "agent")
        graph.add_conditional_edges(
            "agent",
            lambda state: "tools" if state["messages"][-1].get("tool_calls") else END,
        )
        graph.add_edge("tools", "agent")
        self.graph = graph.compile(checkpointer=checkpointer)

    def run(self, actor_id: str, thread_id: str, message: str) -> dict:
        self._claim_thread(actor_id, thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        if self.graph.get_state(config).next:
            raise ValueError("thread is waiting for confirmation")
        result = self.graph.invoke(
            {"messages": [{"role": "user", "content": message}]}, config=config
        )
        output = self._result(thread_id, result)
        self._record_audit(actor_id, thread_id, "run", output)
        return output

    def confirm(self, actor_id: str, thread_id: str, approved: bool) -> dict:
        self._require_owner(actor_id, thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        if not self.graph.get_state(config).next:
            raise ValueError("thread is not waiting for confirmation")
        result = self.graph.invoke(
            Command(resume={"approved": approved}), config=config
        )
        output = self._result(thread_id, result)
        self._record_audit(
            actor_id, thread_id, "confirm", output, {"approved": approved}
        )
        return output

    def list_audit(
        self, actor_id: str, thread_id: str, allow_other: bool = False
    ) -> list[dict]:
        self._require_owner(actor_id, thread_id, allow_other)
        if not self._audit_engine:
            return [
                entry for entry in self._audit_entries
                if entry["thread_id"] == thread_id
            ]
        with self._audit_engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT event_id, thread_id, actor_id, event_type, status, used_tools,
                           details, created_at
                    FROM agent_audit_events
                    WHERE thread_id = :thread_id
                    ORDER BY event_id
                    """
                ),
                {"thread_id": thread_id},
            ).mappings().all()
        return [dict(row) for row in rows]

    def close(self) -> None:
        if self._checkpoint_connection:
            self._checkpoint_connection.close()
        if self._audit_engine:
            self._audit_engine.dispose()

    def check_ready(self) -> None:
        if self._checkpoint_connection:
            self._checkpoint_connection.execute("SELECT 1").fetchone()

    def _ensure_audit_schema(self) -> None:
        with self._audit_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS agent_threads (
                        thread_id TEXT PRIMARY KEY,
                        actor_id TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS agent_audit_events (
                        event_id BIGSERIAL PRIMARY KEY,
                        thread_id TEXT NOT NULL REFERENCES agent_threads(thread_id)
                            ON DELETE CASCADE,
                        actor_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        used_tools JSONB NOT NULL,
                        details JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS agent_audit_thread_id_idx
                    ON agent_audit_events (thread_id, event_id)
                    """
                )
            )

    def _claim_thread(self, actor_id: str, thread_id: str) -> None:
        if not self._audit_engine:
            owner = self._thread_actors.setdefault(thread_id, actor_id)
        else:
            with self._audit_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO agent_threads (thread_id, actor_id)
                        VALUES (:thread_id, :actor_id)
                        ON CONFLICT (thread_id) DO NOTHING
                        """
                    ),
                    {"thread_id": thread_id, "actor_id": actor_id},
                )
                owner = connection.execute(
                    text("SELECT actor_id FROM agent_threads WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                ).scalar_one()
        if owner != actor_id:
            raise PermissionError("thread belongs to another actor")

    def _require_owner(
        self, actor_id: str, thread_id: str, allow_other: bool = False
    ) -> None:
        if not self._audit_engine:
            owner = self._thread_actors.get(thread_id)
        else:
            with self._audit_engine.connect() as connection:
                owner = connection.execute(
                    text("SELECT actor_id FROM agent_threads WHERE thread_id = :thread_id"),
                    {"thread_id": thread_id},
                ).scalar_one_or_none()
        if owner is None:
            raise ValueError("thread not found")
        if not allow_other and owner != actor_id:
            raise PermissionError("thread belongs to another actor")

    def _record_audit(
        self,
        actor_id: str,
        thread_id: str,
        event_type: str,
        output: dict,
        details: dict | None = None,
    ) -> None:
        details = details or {}
        if output.get("pending_action"):
            details["pending_action"] = output["pending_action"]
        entry = {
            "event_id": len(self._audit_entries) + 1,
            "thread_id": thread_id,
            "actor_id": actor_id,
            "event_type": event_type,
            "status": output["status"],
            "used_tools": output.get("used_tools", []),
            "details": details,
            "created_at": datetime.now(timezone.utc),
        }
        if not self._audit_engine:
            self._audit_entries.append(entry)
            return
        with self._audit_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO agent_audit_events (
                        thread_id, actor_id, event_type, status, used_tools, details
                    ) VALUES (
                        :thread_id, :actor_id, :event_type, :status,
                        CAST(:used_tools AS JSONB), CAST(:details AS JSONB)
                    )
                    """
                ),
                {
                    "thread_id": thread_id,
                    "actor_id": actor_id,
                    "event_type": event_type,
                    "status": output["status"],
                    "used_tools": json.dumps(output.get("used_tools", [])),
                    "details": json.dumps(details, ensure_ascii=False),
                },
            )

    def _call_model(self, state: AgentState) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是企业业务助手，必须通过工具获取知识库、订单和库存事实。"
                        "订单与库存是演示数据。取消订单只能生成草稿并等待人工确认；"
                        "即使草稿获批，也不得声称订单已实际取消。"
                    ),
                },
                *state["messages"],
            ],
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.1,
        )
        message = response.choices[0].message
        output = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            output["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in message.tool_calls
            ]
        return {"messages": [output]}

    def _run_tools(self, state: AgentState) -> dict:
        messages = []
        for call in state["messages"][-1]["tool_calls"]:
            name = call["function"]["name"]
            arguments = json.loads(call["function"]["arguments"])
            if name == "knowledge_search":
                result = self.knowledge_search(arguments["question"])
            elif name == "get_order":
                result = DEMO_ORDERS.get(arguments["order_id"], {"error": "订单不存在"})
            elif name == "get_inventory":
                result = DEMO_INVENTORY.get(arguments["sku"], {"error": "SKU 不存在"})
            elif name == "draft_order_cancellation":
                if arguments["order_id"] not in DEMO_ORDERS:
                    result = {"error": "订单不存在，未生成草稿"}
                else:
                    draft = {
                        "action": "cancel_order",
                        "order_id": arguments["order_id"],
                        "reason": arguments["reason"],
                        "executed": False,
                    }
                    review = interrupt(
                        {
                            "type": "confirmation",
                            "message": "是否批准此取消订单草稿？",
                            "draft": draft,
                        }
                    )
                    result = {
                        **draft,
                        "approved": bool(review.get("approved")),
                        "note": "草稿已记录但未执行任何订单操作。",
                    }
            else:
                result = {"error": f"unsupported tool: {name}"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return {
            "messages": messages,
            "used_tools": [message["name"] for message in messages],
        }

    @staticmethod
    def _result(thread_id: str, state: dict) -> dict:
        interrupts = state.get("__interrupt__", ())
        if interrupts:
            return {
                "thread_id": thread_id,
                "status": "needs_confirmation",
                "pending_action": interrupts[0].value,
            }
        return {
            "thread_id": thread_id,
            "status": "completed",
            "answer": state["messages"][-1]["content"],
            "used_tools": state.get("used_tools", []),
        }
