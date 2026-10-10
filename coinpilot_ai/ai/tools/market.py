"""行情工具复用 ChartFeed、MarketCache、HistoryLoader 与指标计算队列。"""

from collections.abc import Callable
import json
import time
from typing import TYPE_CHECKING
import uuid

from PyQt6.QtCore import QTimer

from coinpilot_ai.core.store import encode
from coinpilot_ai.market.buffer import CandleBuffer, CandleRow, candles as valid_candles
from coinpilot_ai.market.cache import HistoryLoader
from coinpilot_ai.market.intervals import BARS, floor_time, shift
from coinpilot_ai.market.indicators import calculate
from coinpilot_ai.indicators.conditions import spec_record
from coinpilot_ai.indicators.validation import runtime_instance
from coinpilot_ai.trading.models import instrument_id
from ..models import JsonValue, validate_integer, validate_payload, validate_text
from ..transport import RequestHandle
from .base import ToolCallback, ToolContext, ToolDefinition, ToolResult

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


def payload(value: object) -> dict[str, JsonValue]:
    decoded: object = json.loads(encode(value))
    return validate_payload(decoded)


def immediate(operation: Callable[[], ToolResult], callback: ToolCallback) -> RequestHandle:
    active = [True]
    def run() -> None:
        if not active[0]:
            return
        active[0] = False
        try:
            result = operation()
        except (ValueError, TypeError, KeyError, ArithmeticError):
            result = ToolResult.failure("数据缺失或参数无效，请检查工具输入")
        callback(result)
    QTimer.singleShot(0, run)
    return RequestHandle(uuid.uuid4().hex, lambda: active.__setitem__(0, False))


def schema(properties: dict[str, JsonValue], required: tuple[str, ...] = ()) -> dict[str, JsonValue]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


INSTRUMENT: dict[str, JsonValue] = {"type": "string", "minLength": 1, "maxLength": 80}
LIMIT: dict[str, JsonValue] = {"type": "integer", "minimum": 1, "maximum": 500}
BAR: dict[str, JsonValue] = {"type": "string", "enum": list(BARS)}
END: dict[str, JsonValue] = {"type": "integer", "minimum": 1, "description": "结束 K 线开盘时间，Unix 秒；不得晚于当前已收盘 K 线"}


class MarketTools:
    def __init__(self, service: "CockpitService") -> None:
        self.service = service

    def definitions(self) -> list[ToolDefinition]:
        candle_schema = schema({"instrument": INSTRUMENT, "bar": BAR, "limit": LIMIT, "end": END}, ("instrument", "bar"))
        return [
            ToolDefinition("get_market_snapshot", "读取报价与来源、新鲜度，不补造缺失行情", schema({"instrument": INSTRUMENT}, ("instrument",)), "market", self.snapshot),
            ToolDefinition("get_candles", "读取最多 500 根已收盘 K 线，优先缓存，缺口通过共享历史链补齐", candle_schema, "market", self.candles),
            ToolDefinition("get_indicators", "计算目录中的内置或自定义指标，保留已收盘、预热和布尔输出状态", schema({
                "instrument": INSTRUMENT, "bar": BAR, "limit": LIMIT, "end": END,
                "kind": {"type": "string", "enum": ["MA", "EMA", "RSI", "ATR", "MACD", "BOLL", "DONCHIAN", "VWAP", "VOLUME_MA", "STOCHASTIC", "SUPERTREND"]},
                "params": {"type": "object"},
                "definition": {"type": "string", "maxLength": 80},
                "definition_revision": {"type": "integer", "minimum": 1},
                "inputs": {"type": "object"},
            }, ("instrument", "bar")), "market", self.indicators),
            ToolDefinition("get_derivatives", "读取现有 Funding/OI/Mark/Index/Basis 快照，注明过期或缺失", schema({"instrument": INSTRUMENT}, ("instrument",)), "market", self.derivatives),
        ]

    def snapshot(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        def operation() -> ToolResult:
            inst = instrument_id(validate_text(arguments.get("instrument"), "合约", nonempty=True))
            quote = self.service.quotes.get(inst)
            if not quote:
                return ToolResult.failure("该合约没有报价")
            now = time.time()
            fresh = -5 <= now - float(quote.get("time", 0)) <= 30
            data = {key: quote[key] for key in ("price", "time", "source", "instrument") if key in quote}
            return ToolResult(True, now, "okx", "live" if fresh else "stale",
                              () if fresh else ("报价已过期，不能作为当前价格",), payload(data))
        return immediate(operation, callback)

    def derivatives(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        def operation() -> ToolResult:
            inst = instrument_id(validate_text(arguments.get("instrument"), "合约", nonempty=True))
            values = self.service.derivatives.values.get(inst, {})
            now = time.time()
            if not values:
                return ToolResult.failure("衍生品数据尚未获取")
            stale = any(not -5 <= now - row.get("time", 0) <= 120 for row in values.values())
            return ToolResult(True, now, "okx", "stale" if stale else "live",
                              ("部分衍生品数据过期",) if stale else (), payload(values))
        return immediate(operation, callback)

    def _load(self, context: ToolContext, arguments: dict[str, JsonValue], callback: Callable[[CandleBuffer | None, str], None]) -> RequestHandle:
        inst = instrument_id(validate_text(arguments.get("instrument"), "合约", nonempty=True))
        bar = validate_text(arguments.get("bar"), "周期", nonempty=True)
        if bar not in BARS:
            raise ValueError("无效周期")
        count = validate_integer(arguments.get("limit", 300), "K 线数量", minimum=1)
        if count > 500 or context.scope.startswith("replay:"):
            raise ValueError("回放数据必须通过回放作用域读取，不能请求实时或未来行情")
        newest = shift(floor_time(int(context.captured_at * 1000), bar), bar, -1)
        end_value = arguments.get("end")
        end = newest if end_value is None else validate_integer(end_value, "结束时间", minimum=1) * 1000
        if end > newest:
            raise ValueError("不得读取未来或未收盘 K 线")
        end = floor_time(end, bar)
        begin = shift(end, bar, -count + 1)
        active = [True]
        memory = valid_candles(self.service.candles.get((inst, bar), ()))
        selected = CandleBuffer(row for row in memory if begin <= row[0] <= end and row[8])
        expected = [shift(begin, bar, index) for index in range(count)]
        if [row[0] for row in selected] == expected:
            QTimer.singleShot(0, lambda: callback(selected, "") if active[0] else None)
            return RequestHandle(uuid.uuid4().hex, lambda: active.__setitem__(0, False))
        loader = HistoryLoader(self.service, self.service.market_cache, self.service)
        def finished(data, error) -> None:
            try:
                if active[0]:
                    callback(CandleBuffer(data) if data is not None and not error else None,
                             "历史行情缺失或加载失败" if error else "")
            finally:
                if data is not None:
                    data.close()
                loader.deleteLater()
        def cancel() -> None:
            active[0] = False
            loader.cancel()
            loader.deleteLater()
        loader.finished.connect(finished)
        loader.load(inst, bar, begin, end)
        return RequestHandle(uuid.uuid4().hex, cancel)

    def candles(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        def ready(rows: CandleBuffer | None, error: str) -> None:
            callback(ToolResult.failure(error or "K 线数据不可用") if rows is None else ToolResult(
                True, context.captured_at, "okx", "historical", (), payload({
                    "instrument": arguments["instrument"], "bar": arguments["bar"],
                    "columns": ["timestamp_ms", "open", "high", "low", "close", "volume", "volume_ccy", "volume_quote", "confirmed"],
                    "candles": [list(row) for row in rows],
                }),
            ))
        return self._load(context, arguments, ready)

    def indicators(self, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        if "definition" in arguments and ("kind" in arguments or "params" in arguments):
            raise ValueError("请选择定义引用或旧指标参数中的一种")
        candidate = {key: value for key, value in arguments.items()
                     if key in ("kind", "params", "definition", "definition_revision", "inputs")}
        instance = runtime_instance(candidate, self.service.indicator_catalog)
        spec = spec_record(instance)
        bar = validate_text(arguments["bar"], "K 线周期", nonempty=True)
        job: int | None = None
        active = [True]
        def ready(rows: CandleBuffer | None, error: str) -> None:
            nonlocal job
            if rows is None:
                callback(ToolResult.failure(error or "指标所需 K 线不可用"))
                return
            def operation() -> dict[str, JsonValue]:
                result = calculate(rows, instance, bar=bar, catalog=self.service.indicator_catalog)
                latest = {name: (result.times[-1], values[-1]) if values and values[-1] is not None else None
                          for name, values in result.outputs.items()}
                return payload({"instrument": arguments["instrument"], "bar": arguments["bar"],
                                "spec": spec, "latest": latest, "sample_count": len(rows),
                                "output_types": result.metadata.output_types,
                                "warming": any(value is None for value in latest.values())})
            def computed(value: object, error: object) -> None:
                if active[0]:
                    callback(ToolResult.failure("指标计算失败") if error else ToolResult(
                        True, context.captured_at, "coinpilot_indicators", "historical", (), validate_payload(value),
                    ))
            job = self.service.compute.submit(operation, computed)
        loading = self._load(context, arguments, ready)
        def cancel() -> None:
            active[0] = False
            loading.cancel()
            if job is not None:
                self.service.compute.cancel(job)
        return RequestHandle(uuid.uuid4().hex, cancel)
