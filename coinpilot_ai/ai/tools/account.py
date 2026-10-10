import time
from typing import TYPE_CHECKING

from ..models import JsonValue
from ..transport import RequestHandle
from .base import ToolCallback, ToolContext, ToolDefinition, ToolResult
from .market import immediate, payload, schema

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


class AccountTools:
    def __init__(self, service: "CockpitService") -> None:
        self.service = service

    def definitions(self) -> list[ToolDefinition]:
        return [ToolDefinition(name, description, schema({}), "account", handler) for name, description, handler in (
            ("get_account_summary", "读取账户权益、可用余额与账户模式，不包含账户标识或凭据", self.summary),
            ("get_positions", "读取当前仓位与新鲜度", self.positions),
            ("get_open_orders", "读取当前普通挂单和保护单，与交易所操作无关", self.orders),
        )]

    def _result(self, data: dict[str, JsonValue]) -> ToolResult:
        now = time.time()
        stamp = float(self.service.account.get("time", 0))
        fresh = 0 <= now - stamp <= 20
        return ToolResult(bool(stamp), now, "coinpilot_paper" if self.service.environment == "paper" else "okx",
                          "live" if fresh else "stale" if stamp else "missing",
                          () if fresh else ("账户数据过期或未连接",), data)

    def summary(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        return immediate(lambda: self._result(payload({
            "account": {key: self.service.account[key] for key in ("posMode", "acctLv", "time") if key in self.service.account},
            "balance": {key: self.service.balance[key] for key in ("totalEq", "availEq", "adjEq", "isoEq", "upl") if key in self.service.balance},
        })), callback)

    def positions(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        fields = ("instId", "posSide", "pos", "availPos", "avgPx", "upl", "uplRatio", "lever", "mgnMode", "liqPx")
        return immediate(lambda: self._result(payload({"positions": [
            {key: row[key] for key in fields if key in row} for row in self.service.positions
        ]})), callback)

    def orders(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        fields = ("instId", "side", "posSide", "ordType", "sz", "px", "state", "tdMode", "cTime", "tpTriggerPx", "slTriggerPx")
        return immediate(lambda: self._result(payload({"orders": [
            {key: row[key] for key in fields if key in row} for row in self.service.pending_orders + self.service.algos
        ]})), callback)
