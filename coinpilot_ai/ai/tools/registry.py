from collections.abc import Callable
import json
import math
import uuid

from PyQt6.QtCore import QObject, QTimer

from ..models import JsonValue, validate_integer, validate_payload
from ..providers.base import ToolCall
from ..transport import RequestHandle
from .base import ToolCallback, ToolContext, ToolDefinition, ToolResult


def validate_arguments(arguments: object, schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    clean = validate_payload(arguments)
    properties, required = schema.get("properties", {}), schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ValueError("工具 schema 无效")
    if set(clean) - set(properties) or any(name not in clean for name in required if isinstance(name, str)):
        raise ValueError("工具参数缺失或存在未知字段")
    for name, value in clean.items():
        field = properties[name]
        if not isinstance(field, dict):
            raise ValueError("工具 schema 无效")
        kind = field.get("type")
        if kind == "string":
            if not isinstance(value, str):
                raise ValueError("工具参数须为字符串")
            minimum, maximum = field.get("minLength", 0), field.get("maxLength", 4000)
            if not isinstance(minimum, int) or not isinstance(maximum, int) or not minimum <= len(value) <= maximum:
                raise ValueError("工具参数字符串长度无效")
        elif kind in ("integer", "number"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("工具参数须为有限数值")
            if kind == "integer" and not isinstance(value, int):
                raise ValueError("工具参数须为整数")
            for bound, compare in (("minimum", lambda a, b: a >= b), ("maximum", lambda a, b: a <= b)):
                threshold = field.get(bound)
                if threshold is not None and (not isinstance(threshold, (int, float)) or not compare(value, threshold)):
                    raise ValueError("工具参数超出范围")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError("工具参数须为布尔值")
        elif kind == "object":
            if not isinstance(value, dict):
                raise ValueError("工具参数须为对象")
        elif kind == "array":
            if not isinstance(value, list) or len(value) > 50:
                raise ValueError("工具参数数组无效或过长")
        else:
            raise ValueError("工具参数类型无效")
        choices = field.get("enum")
        if isinstance(choices, list) and value not in choices:
            raise ValueError("工具参数不在允许值中")
    return clean


class ToolRegistry(QObject):
    MAX_RESULT_BYTES = 64 * 1024

    def __init__(self, current_scope: Callable[[], str], parent: QObject | None = None, *, timeout_ms: int = 15000) -> None:
        super().__init__(parent)
        self.current_scope = current_scope
        self.timeout_ms = timeout_ms
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError("重复的工具名称")
        if definition.name in ("submit_order", "cancel_order", "close_position", "set_leverage"):
            raise ValueError("AI 不允许注册实际交易工具")
        validate_payload(definition.input_schema)
        self._definitions[definition.name] = definition

    @staticmethod
    def _allowed(definition: ToolDefinition, context: ToolContext) -> bool:
        return (definition.permission not in ("account", "history")
                or definition.permission == "account" and context.allow_account
                or definition.permission == "history" and context.allow_history)

    def schemas(self, context: ToolContext) -> tuple[dict[str, JsonValue], ...]:
        return tuple({"name": definition.name, "description": definition.description,
                      "parameters": definition.input_schema} for definition in self._definitions.values()
                     if self._allowed(definition, context))

    def execute(self, call: ToolCall, context: ToolContext, callback: ToolCallback, *, timeout_ms: int | None = None) -> RequestHandle:
        duration = self.timeout_ms if timeout_ms is None else min(self.timeout_ms, validate_integer(timeout_ms, "工具超时", minimum=1))
        completed = False
        child: RequestHandle | None = None
        timer = QTimer(self)
        timer.setSingleShot(True)
        identifier = uuid.uuid4().hex

        def finish(result: ToolResult) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            timer.stop()
            timer.deleteLater()
            if context.scope != self.current_scope():
                result = ToolResult.failure("环境已切换，工具结果已丢弃")
            try:
                encoded = json.dumps(result.payload(), ensure_ascii=False, allow_nan=False).encode()
                if len(encoded) > self.MAX_RESULT_BYTES:
                    result = ToolResult.failure("工具结果超过 64 KB，请缩小范围")
            except (ValueError, TypeError, RecursionError):
                result = ToolResult.failure("工具返回数据无效")
            callback(result)

        def cancel() -> None:
            finish(ToolResult.failure("工具已取消"))
            if child is not None:
                child.cancel()

        def timeout() -> None:
            finish(ToolResult.failure("工具执行超时"))
            if child is not None:
                child.cancel()

        handle = RequestHandle(identifier, cancel)
        timer.timeout.connect(timeout)
        definition = self._definitions.get(call.name)
        try:
            if context.scope != self.current_scope():
                raise ValueError("环境已切换，工具请求已拒绝")
            if definition is None:
                raise ValueError("AI 请求了不存在的工具")
            if not self._allowed(definition, context):
                raise ValueError("未授权读取该类数据，请在设置中明确开启")
            arguments = validate_arguments(call.arguments, definition.input_schema)
        except ValueError as error:
            message = str(error)
            QTimer.singleShot(0, lambda: finish(ToolResult.failure(message)))
            return handle
        timer.start(duration)
        try:
            child = definition.handler(context, arguments, finish)
        except Exception:
            finish(ToolResult.failure("工具执行失败，请检查数据是否可用"))
        return handle
