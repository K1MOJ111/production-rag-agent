import json
from uuid import uuid4

from sqlalchemy import create_engine, text


def _identifier(value: str, label: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 64:
        raise ValueError(f"{label} must be 1-64 characters")
    return value


def _reason(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 500:
        raise ValueError("cancellation reason must be 1-500 characters")
    return value


class InMemoryBusinessAdapter:
    records_audit = False

    def __init__(self, orders: dict, inventory: dict) -> None:
        self.orders = {key: dict(value) for key, value in orders.items()}
        self.inventory = {key: dict(value) for key, value in inventory.items()}
        self.cancellations: dict[str, dict] = {}

    def get_order(self, order_id: str) -> dict:
        order_id = _identifier(order_id, "order_id")
        order = self.orders.get(order_id)
        return {"order_id": order_id, **order} if order else {"error": "订单不存在"}

    def get_inventory(self, sku: str) -> dict:
        sku = _identifier(sku, "sku")
        item = self.inventory.get(sku)
        return {"sku": sku, **item} if item else {"error": "SKU 不存在"}

    def draft_cancellation(self, order_id: str, reason: str) -> dict:
        order = self.get_order(order_id)
        if "error" in order:
            return {"error": "订单不存在，未生成草稿"}
        if order["status"] != "待处理":
            return {"error": "当前订单状态不允许取消，未生成草稿"}
        return {
            "action": "cancel_order",
            "order_id": order["order_id"],
            "reason": _reason(reason),
            "executed": False,
        }

    def approve_cancellation(
        self,
        actor_id: str,
        thread_id: str,
        draft: dict,
        request_id: str,
    ) -> dict:
        del actor_id
        order_id = _identifier(draft["order_id"], "order_id")
        reason = _reason(draft["reason"])
        key = f"{thread_id}:cancel_order:{order_id}"
        if key in self.cancellations:
            return {**self.cancellations[key], "idempotent": True}
        if self.orders[order_id]["status"] != "待处理":
            raise ValueError("current order status does not allow cancellation")
        self.orders[order_id]["status"] = "已取消"
        result = {
            "action": "cancel_order",
            "order_id": order_id,
            "reason": reason,
            "approved": True,
            "executed": True,
            "cancellation_id": str(uuid4()),
            "request_id": request_id,
            "note": "已写入本地业务数据；未调用外部 ERP。",
        }
        self.cancellations[key] = result
        return result

    def check_ready(self) -> None:
        return None

    def close(self) -> None:
        return None


class PostgresBusinessAdapter:
    records_audit = True

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS inventory (
                        sku VARCHAR(64) PRIMARY KEY,
                        available INTEGER NOT NULL CHECK (available >= 0),
                        warehouse TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS orders (
                        order_id VARCHAR(64) PRIMARY KEY,
                        status VARCHAR(16) NOT NULL
                            CHECK (status IN ('待处理', '已发货', '已取消')),
                        sku VARCHAR(64) NOT NULL REFERENCES inventory(sku),
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS cancellation_requests (
                        cancellation_id UUID PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        thread_id TEXT NOT NULL,
                        actor_id TEXT NOT NULL,
                        order_id VARCHAR(64) NOT NULL REFERENCES orders(order_id),
                        reason VARCHAR(500) NOT NULL,
                        request_id TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO inventory (sku, available, warehouse)
                    VALUES ('SKU-A100', 18, '深圳仓'), ('SKU-B200', 0, '深圳仓')
                    ON CONFLICT (sku) DO NOTHING
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO orders (order_id, status, sku, quantity)
                    VALUES
                        ('ORD-1001', '已发货', 'SKU-A100', 2),
                        ('ORD-1002', '待处理', 'SKU-B200', 1)
                    ON CONFLICT (order_id) DO NOTHING
                    """
                )
            )

    def get_order(self, order_id: str) -> dict:
        order_id = _identifier(order_id, "order_id")
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT order_id, status, sku, quantity
                    FROM orders WHERE order_id = :order_id
                    """
                ),
                {"order_id": order_id},
            ).mappings().first()
        return dict(row) if row else {"error": "订单不存在"}

    def get_inventory(self, sku: str) -> dict:
        sku = _identifier(sku, "sku")
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT sku, available, warehouse
                    FROM inventory WHERE sku = :sku
                    """
                ),
                {"sku": sku},
            ).mappings().first()
        return dict(row) if row else {"error": "SKU 不存在"}

    def draft_cancellation(self, order_id: str, reason: str) -> dict:
        order = self.get_order(order_id)
        if "error" in order:
            return {"error": "订单不存在，未生成草稿"}
        if order["status"] != "待处理":
            return {"error": "当前订单状态不允许取消，未生成草稿"}
        return {
            "action": "cancel_order",
            "order_id": order["order_id"],
            "reason": _reason(reason),
            "executed": False,
        }

    def approve_cancellation(
        self,
        actor_id: str,
        thread_id: str,
        draft: dict,
        request_id: str,
    ) -> dict:
        order_id = _identifier(draft["order_id"], "order_id")
        reason = _reason(draft["reason"])
        key = f"{thread_id}:cancel_order:{order_id}"
        with self._engine.begin() as connection:
            owner = connection.execute(
                text(
                    "SELECT actor_id FROM agent_threads "
                    "WHERE thread_id = :thread_id FOR UPDATE"
                ),
                {"thread_id": thread_id},
            ).scalar_one_or_none()
            if owner != actor_id:
                raise PermissionError("thread belongs to another actor")

            existing = connection.execute(
                text(
                    """
                    SELECT cancellation_id, request_id
                    FROM cancellation_requests
                    WHERE idempotency_key = :idempotency_key
                    """
                ),
                {"idempotency_key": key},
            ).mappings().first()
            if existing:
                return {
                    "action": "cancel_order",
                    "order_id": order_id,
                    "reason": reason,
                    "approved": True,
                    "executed": True,
                    "cancellation_id": str(existing["cancellation_id"]),
                    "request_id": existing["request_id"],
                    "idempotent": True,
                    "note": "本地业务写入已存在；未重复执行。",
                }

            status = connection.execute(
                text(
                    "SELECT status FROM orders "
                    "WHERE order_id = :order_id FOR UPDATE"
                ),
                {"order_id": order_id},
            ).scalar_one_or_none()
            if status is None:
                raise ValueError("order not found")
            if status != "待处理":
                raise ValueError("current order status does not allow cancellation")

            cancellation_id = str(uuid4())
            connection.execute(
                text(
                    """
                    UPDATE orders SET status = '已取消', updated_at = NOW()
                    WHERE order_id = :order_id
                    """
                ),
                {"order_id": order_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO cancellation_requests (
                        cancellation_id, idempotency_key, thread_id, actor_id,
                        order_id, reason, request_id
                    ) VALUES (
                        :cancellation_id, :idempotency_key, :thread_id, :actor_id,
                        :order_id, :reason, :request_id
                    )
                    """
                ),
                {
                    "cancellation_id": cancellation_id,
                    "idempotency_key": key,
                    "thread_id": thread_id,
                    "actor_id": actor_id,
                    "order_id": order_id,
                    "reason": reason,
                    "request_id": request_id,
                },
            )
            details = {
                "request_id": request_id,
                "action": "cancel_order",
                "result": "executed",
                "order_id": order_id,
                "cancellation_id": cancellation_id,
            }
            connection.execute(
                text(
                    """
                    INSERT INTO agent_audit_events (
                        thread_id, actor_id, event_type, status, used_tools, details
                    ) VALUES (
                        :thread_id, :actor_id, 'confirm', 'completed',
                        CAST(:used_tools AS JSONB), CAST(:details AS JSONB)
                    )
                    """
                ),
                {
                    "thread_id": thread_id,
                    "actor_id": actor_id,
                    "used_tools": json.dumps(["draft_order_cancellation"]),
                    "details": json.dumps(details, ensure_ascii=False),
                },
            )
        return {
            "action": "cancel_order",
            "order_id": order_id,
            "reason": reason,
            "approved": True,
            "executed": True,
            "cancellation_id": cancellation_id,
            "request_id": request_id,
            "note": "已写入本地业务数据库；未调用外部 ERP。",
        }

    def check_ready(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()

    def close(self) -> None:
        self._engine.dispose()
