"""AI 指标目录和可编辑草稿；工具本身不保存定义或修改图表。"""
from dataclasses import asdict
from typing import TYPE_CHECKING
import uuid

from coinpilot_ai.indicators.script.compiler import compile_source
from coinpilot_ai.indicators.script.diagnostics import ScriptError
from ..models import JsonValue, validate_integer, validate_text
from ..transport import RequestHandle
from .base import ToolCallback, ToolContext, ToolDefinition, ToolResult
from .market import immediate, payload, schema

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService

IDENTITY: dict[str, JsonValue] = {"type": "string", "minLength": 1, "maxLength": 80}
SOURCE: dict[str, JsonValue] = {"type": "string", "minLength": 1, "maxLength": 16000,
    "description": "Pine v5/v6 基础指标子集；indicator、input、ta、math、plot/plotshape/hline/bgcolor。拒绝交易、网络、导入、循环、request.security、复杂对象。"}


class IndicatorTools:
    def __init__(self, service: "CockpitService") -> None:
        self.service = service

    def definitions(self) -> list[ToolDefinition]:
        script = schema({"source": SOURCE, "description": {"type": "string", "maxLength": 2000}}, ("source",))
        return [
            ToolDefinition("list_indicators", "分页查询指标标识、版本与输入／输出名称，详情通过 get_indicator_definition 读取", schema({
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), "market", self.list_indicators),
            ToolDefinition("get_indicator_definition", "查询指定版本及用户保存的源码，用于解释、修改或修复", schema({
                "definition": IDENTITY, "revision": {"type": "integer", "minimum": 1},
                "source_offset": {"type": "integer", "minimum": 0}}, ("definition",)), "market", self.get_definition),
            ToolDefinition("validate_indicator_script", "后台编译指标，返回行列诊断和元数据，不保存或运行交易", script, "market", self.validate),
            ToolDefinition("create_indicator_draft", "生成可编辑指标代码卡片，用户预览确认后才能保存或添加", script, "market", self.draft),
            ToolDefinition("apply_indicator_to_chart", "准备指标代码卡片供用户点击预览并添加；不会自动修改图表", script, "market", self.draft),
        ]

    def list_indicators(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        def operation() -> ToolResult:
            offset = validate_integer(arguments.get("offset", 0), "起始项")
            limit = validate_integer(arguments.get("limit", 20), "数量", minimum=1)
            definitions = self.service.indicator_catalog.list()
            selected = definitions[offset:offset+limit]
            return ToolResult(True, context.captured_at, "indicator_catalog", "historical", (), payload({
                "definitions": [{"id": item.id, "name": item.name, "revision": item.revision, "source": item.source,
                    "inputs": [field.id for field in item.inputs],
                    "outputs": [{"id": field.id, "type": field.type} for field in item.outputs]} for item in selected],
                "total": len(definitions), "next_offset": offset+limit if offset+limit < len(definitions) else None}))
        return immediate(operation, callback)

    def get_definition(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        def operation() -> ToolResult:
            identity = validate_text(arguments.get("definition"), "定义标识", nonempty=True)
            revision = arguments.get("revision")
            definition = self.service.indicator_catalog.resolve(identity,
                None if revision is None else validate_integer(revision, "版本", minimum=1))
            source = None if definition.source == "builtin" else self.service.indicator_library.source(identity, definition.revision)
            offset = validate_integer(arguments.get("source_offset", 0), "源码起始位置")
            length = len(source) if source is not None else 0
            return ToolResult(True, context.captured_at, "indicator_catalog", "historical", (),
                              payload({"definition": asdict(definition), "source": source[offset:offset+8000] if source is not None else None,
                                       "source_length": length, "next_source_offset": offset+8000 if offset+8000 < length else None}))
        return immediate(operation, callback)

    def compile(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback,
                *, draft: bool) -> RequestHandle:
        source = validate_text(arguments.get("source"), "指标源码", nonempty=True)
        description = validate_text(arguments.get("description", ""), "指标说明")
        active = [True]
        def operation() -> ToolResult:
            data: dict[str, object] = {"valid": False, "diagnostics": []}
            if draft:
                data.update(source=source, description=description, requires_confirmation=True)
            try:
                program = compile_source(source, language="ai")
                data.update(valid=True, definition=asdict(program.definition), source_hash=program.source_hash)
            except ScriptError as exc:
                data["diagnostics"] = [asdict(exc.diagnostic)]
            # 错误源码仍可显示为草稿，在同一编辑器修复；不注册运行时程序。
            return ToolResult(True, context.captured_at, "indicator_compiler", "historical", (), payload(data))
        def ready(value: object, error: object) -> None:
            if active[0]:
                callback(value if isinstance(value, ToolResult) and not error else ToolResult.failure("指标编译失败"))
        job = self.service.compute.submit(operation, ready)
        def cancel() -> None:
            active[0] = False
            self.service.compute.cancel(job)
        return RequestHandle(uuid.uuid4().hex, cancel)

    def validate(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        return self.compile(context, arguments, callback, draft=False)

    def draft(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        return self.compile(context, arguments, callback, draft=True)
