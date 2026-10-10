import time

from coinpilot_ai.trading.models import OrderDraft, instrument_id, number
from ..models import JsonValue
from ..transport import RequestHandle
from .base import ToolCallback, ToolContext, ToolDefinition, ToolResult
from .market import INSTRUMENT, immediate, payload, schema


def definition() -> ToolDefinition:
    properties: dict[str, JsonValue] = {key: {"type": "string", "maxLength": 4000 if key == "reason" else 80} for key in (
        "size", "price", "stop_loss", "take_profit", "reason",
    )}
    properties.update(instrument=INSTRUMENT, action={"type": "string", "enum": ["open", "close"]},
                      direction={"type": "string", "enum": ["long", "short"]},
                      order_type={"type": "string", "enum": ["market", "limit"]},
                      margin={"type": "string", "enum": ["cross", "isolated"]})
    return ToolDefinition("create_order_draft", "仅创建可编辑订单草稿；载入面板后仍需程序重新校验与用户确认",
                          schema(properties, ("instrument", "action", "direction", "order_type", "margin")), "order_draft", create)


def create(context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
    def operation() -> ToolResult:
        draft = OrderDraft.from_ai(arguments)
        instrument_id(draft.instrument)
        for field in ("size", "price", "stop_loss", "take_profit"):
            value = getattr(draft, field)
            if value:
                number(value, field, positive=True)
        if draft.price and draft.stop_loss and draft.action == "open":
            entry, stop = number(draft.price), number(draft.stop_loss)
            if (draft.direction == "long" and stop >= entry) or (draft.direction == "short" and stop <= entry):
                raise ValueError("止损方向无效")
        if draft.price and draft.take_profit and draft.action == "open":
            entry, target = number(draft.price), number(draft.take_profit)
            if (draft.direction == "long" and target <= entry) or (draft.direction == "short" and target >= entry):
                raise ValueError("止盈方向无效")
        return ToolResult(True, time.time(), "coinpilot_draft", "historical",
                          ("草稿尚未提交，缺失字段须在订单面板补全",), payload({"draft": draft.to_dict()}))
    return immediate(operation, callback)
