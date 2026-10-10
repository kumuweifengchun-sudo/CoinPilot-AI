from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
import time

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from coinpilot_ai.ai.models import JsonValue, validate_text
from coinpilot_ai.ai.providers.base import ToolCall
from coinpilot_ai.ai.tools.base import ToolCallback, ToolContext, ToolDefinition, ToolResult
from coinpilot_ai.ai.tools.market import immediate, schema
from coinpilot_ai.ai.tools.registry import ToolRegistry
from coinpilot_ai.ai.transport import RequestHandle
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.intervals import floor_time, shift


class EmptyVault:
    def read(self, key: str) -> dict[str, str] | None:
        return None


@pytest.fixture
def business(tmp_path: Path, app: QApplication) -> Iterator[CockpitService]:
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path / "business.sqlite3",
                             tmp_path / "icons", vault=EmptyVault(), autostart=False)
    service.api.get = lambda *args, **kwargs: pytest.fail("离线工具测试不应发起交易所请求")
    service.refresh_account = lambda: None
    service.refresh_market = lambda: None
    try:
        yield service
    finally:
        service.close()
        service.deleteLater()
        app.processEvents()


def execute(service: CockpitService, app: QApplication, name: str, arguments: dict[str, JsonValue],
            *, account: bool = False, history: bool = False) -> ToolResult:
    chat = service.ai_chat
    assert chat is not None
    results: list[ToolResult] = []
    chat.registry.execute(ToolCall("call", name, arguments),
                          ToolContext(service.scope, service.environment, time.time(), account, history), results.append)
    deadline = time.monotonic() + 3
    while not results and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(2)
    assert len(results) == 1
    return results[0]


def test_builtin_tool_set_has_no_actual_trading(business: CockpitService) -> None:
    chat = business.ai_chat
    assert chat is not None
    context = ToolContext(business.scope, business.environment, time.time())
    public_names = {validate_text(schema["name"], "name") for schema in chat.registry.schemas(context)}
    assert "get_candles" in public_names and "create_order_draft" in public_names
    assert "get_account_summary" not in public_names and "get_trade_history" not in public_names
    all_names = {validate_text(schema["name"], "name") for schema in chat.registry.schemas(ToolContext(business.scope, business.environment, time.time(), True, True))}
    assert len(all_names) == 17
    assert not all_names & {"submit_order", "cancel_order", "close_position", "set_leverage"}


@pytest.mark.parametrize("name,arguments", [
    ("unknown", {}), ("get_candles", {"instrument": "BTCUSDT", "bar": "invalid"}),
    ("get_candles", {"instrument": "BTCUSDT", "bar": "1H", "limit": 501}),
    ("get_candles", {"instrument": "BTCUSDT", "bar": "1H", "limit": True}),
    ("get_market_snapshot", {"instrument": "BTCUSDT", "secret": "fake-only"}),
    ("get_candles", {"instrument": "BTCUSDT", "bar": "1H", "end": 9999999999}),
    ("get_indicators", {"instrument": "BTCUSDT", "bar": "1H", "kind": "EMA", "params": {"period": 1.5}}),
    ("create_order_draft", {"instrument": "BTCUSDT", "action": "open", "direction": "long", "order_type": "market", "margin": "cross", "size": "NaN"}),
])
def test_invalid_tool_arguments_are_rejected(business: CockpitService, app: QApplication,
                                           name: str, arguments: dict[str, JsonValue]) -> None:
    assert not execute(business, app, name, arguments).ok


def test_account_permission_and_freshness_and_sensitive_field_filter(business: CockpitService, app: QApplication) -> None:
    assert not execute(business, app, "get_account_summary", {}).ok
    business.account = {"time": time.time(), "posMode": "net_mode", "api_key": "fake-only-secret"}
    business.balance = {"totalEq": "10000", "availEq": "5000", "account_id": "private-account"}
    result = execute(business, app, "get_account_summary", {}, account=True)
    assert result.ok and result.freshness == "live"
    assert "fake-only-secret" not in str(result.payload()) and "private-account" not in str(result.payload())
    business.account["time"] = time.time() - 60
    assert execute(business, app, "get_account_summary", {}, account=True).freshness == "stale"


def test_market_snapshot_marks_stale(business: CockpitService, app: QApplication) -> None:
    business.quotes["BTC-USDT-SWAP"] = {"price": "100", "time": time.time() - 60, "source": "okx"}
    result = execute(business, app, "get_market_snapshot", {"instrument": "BTCUSDT"})
    assert result.ok and result.freshness == "stale" and result.warnings


def test_candles_memory_and_single_bar_shared_cache_and_indicator_warmup(business: CockpitService, app: QApplication) -> None:
    inst, bar = "BTC-USDT-SWAP", "1H"
    end = shift(floor_time(int(time.time() * 1000), bar), bar, -1)
    rows = CandleBuffer((shift(end, bar, index - 19), 100.0, 110.0, 90.0, 101.0, 1.0, 0.0, 0.0, 1) for index in range(20))
    business.candles[(inst, bar)] = rows
    result = execute(business, app, "get_candles", {"instrument": inst, "bar": bar, "limit": 20})
    candles = result.data.get("candles")
    assert result.ok and isinstance(candles, list) and len(candles) == 20
    result = execute(business, app, "get_indicators", {"instrument": inst, "bar": bar, "limit": 20, "kind": "EMA", "params": {"period": 50}})
    assert result.ok and result.data["warming"] is True
    business.market_cache.put(inst, bar, rows)
    business.candles.clear()
    result = execute(business, app, "get_candles", {"instrument": inst, "bar": bar, "limit": 1})
    candles = result.data.get("candles")
    assert result.ok and isinstance(candles, list) and len(candles) == 1


def test_decimal_position_plan_and_editable_draft_only(business: CockpitService, app: QApplication) -> None:
    inst = "BTC-USDT-SWAP"
    business.specs[inst] = {"instId": inst, "ctType": "linear", "settleCcy": "USDT", "ctVal": "0.01", "ctValCcy": "BTC", "lotSz": "1", "minSz": "1"}
    plan = execute(business, app, "calculate_position_plan", {"instrument": inst, "equity": "10000", "risk_percent": "1", "entry": "100", "stop": "90", "target": "120", "direction": "long"})
    assert plan.ok and Decimal(str(plan.data["contracts"])) == 1000 and Decimal(str(plan.data["planned_loss"])) == 100
    draft = execute(business, app, "create_order_draft", {"instrument": inst, "action": "open", "direction": "long", "order_type": "limit", "margin": "cross", "size": "10", "price": "100", "stop_loss": "90", "take_profit": "120"})
    assert draft.ok and isinstance(draft.data["draft"], dict) and draft.data["draft"]["source"] == "ai"
    assert business.trading.prepared == {}
    assert business.store.list("local_order", business.scope) == []


def test_indicator_tools_validate_draft_and_calculate_custom_without_saving(business: CockpitService, app: QApplication) -> None:
    source = '''//@version=6
indicator("AI Signal", overlay=true)
enabled = input.bool(true)
average = ta.ema(close, 3)
buy = enabled and close > average
plot(average)
plotshape(buy)
'''
    before = business.chart_book.indicators.copy()
    draft = execute(business, app, "create_indicator_draft", {"source": source})
    assert draft.ok and draft.data["valid"] is True and draft.data["requires_confirmation"] is True
    assert draft.data["source"] == source
    apply = execute(business, app, "apply_indicator_to_chart", {"source": source})
    assert apply.ok and business.chart_book.indicators == before
    assert not business.store.list("indicator_definition")
    invalid = execute(business, app, "validate_indicator_script", {"source": 'indicator("x")\nplot(request.security("BTC", "1D", close))'})
    assert invalid.ok and invalid.data["valid"] is False and invalid.data["diagnostics"]
    program = business.indicator_library.save(source, language="ai", confirmed=True)
    identity = program.definition.id
    definitions = execute(business, app, "list_indicators", {})
    assert definitions.ok and identity in str(definitions.data)
    definition = execute(business, app, "get_indicator_definition", {"definition": identity, "revision": 1})
    assert definition.ok and definition.data["source"] == source
    inst, bar = "BTC-USDT-SWAP", "1m"
    end = shift(floor_time(int(time.time()*1000), bar), bar, -1)
    business.candles[(inst, bar)] = CandleBuffer((shift(end, bar, i-9), 100., 105., 95., 100.+i*.1, 10., 0., 0., 1)
                                                for i in range(10))
    values = execute(business, app, "get_indicators", {"instrument": inst, "bar": bar, "limit": 10,
        "definition": identity, "definition_revision": 1, "inputs": {"enabled": False}})
    assert values.ok
    latest = values.data["latest"]
    assert isinstance(latest, dict) and isinstance(latest["buy"], list) and latest["buy"][1] is False
    assert values.data["output_types"] == {"average": "float", "buy": "bool"}


def test_history_permissions_scope_and_report_pin(business: CockpitService, app: QApplication) -> None:
    now = time.time()
    business.store.put("event", "event-id", {"time": now, "name": "测试提醒", "instrument": "BTC-USDT-SWAP"}, business.scope)
    business.store.put("event", "other", {"time": now, "name": "别的账户"}, "live:other")
    business.store.put("report", "report-id", {"time": now, "answer": "历史分析", "status": "complete", "api_key": "fake-only"}, business.scope)
    assert not execute(business, app, "get_alerts", {}).ok
    events = execute(business, app, "get_alerts", {"event_id": "event-id"}, history=True)
    assert events.ok and "测试提醒" in str(events.data) and "别的账户" not in str(events.data)
    report = execute(business, app, "get_trade_review", {"report_id": "report-id"}, history=True)
    assert report.ok and "历史分析" in str(report.data) and "fake-only" not in str(report.data)


def test_registry_timeout_size_and_scope_change(app: QApplication) -> None:
    scope = ["demo:tools"]
    registry = ToolRegistry(lambda: scope[0], timeout_ms=10)
    callbacks: list[ToolCallback] = []
    def hanging(context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        callbacks.append(callback)
        return RequestHandle("hanging", lambda: None)
    registry.register(ToolDefinition("hanging", "hang", schema({}), "market", hanging))
    results: list[ToolResult] = []
    context = ToolContext(scope[0], "demo", time.time())
    registry.execute(ToolCall("call", "hanging", {}), context, results.append)
    QTest.qWait(30)
    assert len(results) == 1 and not results[0].ok and "超时" in results[0].warnings[0]
    callbacks[0](ToolResult(True, time.time(), "fake", "live", (), {}))
    assert len(results) == 1
    results.clear()
    registry.execute(ToolCall("next", "hanging", {}), context, results.append)
    scope[0] = "live:tools"
    callbacks[-1](ToolResult(True, time.time(), "fake", "live", (), {"sensitive": "old-scope"}))
    assert not results[0].ok and not results[0].data
    registry.register(ToolDefinition("huge", "large", schema({}), "market", lambda ctx, args, done:
                      immediate(lambda: ToolResult(True, time.time(), "fake", "live", (), {"text": "x" * 65536}), done)))
    results.clear()
    registry.execute(ToolCall("huge", "huge", {}), ToolContext(scope[0], "live", time.time()), results.append)
    QTest.qWait(10)
    assert not results[0].ok and "64 KB" in results[0].warnings[0]
    registry.deleteLater()


def test_tool_uses_smaller_remaining_turn_budget(app: QApplication) -> None:
    registry = ToolRegistry(lambda: "demo:deadline", timeout_ms=15000)
    cancelled: list[bool] = []
    registry.register(ToolDefinition("slow", "slow", schema({}), "market", lambda ctx, args, done:
                                    RequestHandle("slow", lambda: cancelled.append(True))))
    results: list[ToolResult] = []
    registry.execute(ToolCall("call", "slow", {}), ToolContext("demo:deadline", "demo", time.time()),
                     results.append, timeout_ms=10)
    QTest.qWait(30)
    assert len(results) == 1 and not results[0].ok and cancelled == [True]
    registry.deleteLater()
