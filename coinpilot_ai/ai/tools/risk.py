import time
from typing import TYPE_CHECKING

from coinpilot_ai.trading.models import instrument_id
from coinpilot_ai.trading.risk import position_plan
from ..models import JsonValue, validate_text
from ..transport import RequestHandle
from .base import ToolCallback, ToolContext, ToolDefinition, ToolResult
from .market import INSTRUMENT, immediate, payload, schema

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


class RiskTools:
    def __init__(self, service: "CockpitService") -> None:
        self.service = service

    def definitions(self) -> list[ToolDefinition]:
        properties: dict[str, JsonValue] = {key: {"type": "string", "maxLength": 80} for key in (
            "equity", "risk_percent", "entry", "stop", "target",
        )}
        properties.update(instrument=INSTRUMENT, direction={"type": "string", "enum": ["long", "short"]})
        return [ToolDefinition("calculate_position_plan", "按提供的权益与风险比例用 Decimal 计算合约张数；不自动读取账户或下单",
                               schema(properties, ("instrument", "equity", "risk_percent", "entry", "stop", "direction")), "market", self.calculate)]

    def calculate(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        def operation() -> ToolResult:
            inst = instrument_id(validate_text(arguments.get("instrument"), "合约", nonempty=True))
            spec = self.service.specs.get(inst)
            if not spec:
                return ToolResult.failure("合约规格尚未获取")
            result = position_plan(
                spec=spec, equity=arguments["equity"], risk_percent=arguments["risk_percent"],
                entry=arguments["entry"], stop=arguments["stop"], target=arguments.get("target"),
                direction=validate_text(arguments["direction"], "方向", nonempty=True),
            )
            return ToolResult(True, time.time(), "coinpilot_risk", "historical", ("基于用户提供的参数，不代表当前账户状态或实际成交",), payload(result))
        return immediate(operation, callback)
