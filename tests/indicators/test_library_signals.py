from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from coinpilot_ai.core.store import Store
from coinpilot_ai.indicators.catalog import IndicatorCatalog
from coinpilot_ai.indicators.conditions import compare_values, spec_record, validate_condition
from coinpilot_ai.indicators.models import IndicatorInstance
from coinpilot_ai.indicators.runtime.engine import evaluate
from coinpilot_ai.indicators.script.compiler import compile_source
from coinpilot_ai.indicators.store import IndicatorLibrary
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.indicators import IndicatorRegistry
from coinpilot_ai.research.backtest import run_backtest
from coinpilot_ai.research.scanner import indicator_match
from coinpilot_ai.research.replay import ReplaySession
from coinpilot_ai.trading.alerts import AlertEngine, validate_rule
from coinpilot_ai.trading.dialogs import IndicatorConditionDialog

SOURCE = '''//@version=6
indicator("Signal", overlay=true)
enabled = input.bool(true)
level = input.float(105, minval=0, step=0.5)
buy = enabled and close > level
price = ta.ema(close, 3)
plot(price)
plotshape(buy)
'''


def candles() -> CandleBuffer:
    return CandleBuffer((60000*(index+1), close, close+1, close-1, close, 10., 0., 0., 1)
                        for index, close in enumerate((100., 100., 110., 100., 110., 111., 110., 110.)))


def catalog_instance() -> tuple[IndicatorCatalog, IndicatorInstance]:
    catalog = IndicatorCatalog()
    program = compile_source(SOURCE, identity="custom.signal")
    catalog.register_program(program)
    return catalog, IndicatorInstance("signal", "custom.signal", 1)


def test_library_versions_references_restart_and_corruption(tmp_path: Path) -> None:
    store = Store(tmp_path / "library.sqlite3")
    catalog = IndicatorCatalog()
    library = IndicatorLibrary(store, catalog)
    try:
        with pytest.raises(ValueError, match="用户确认"):
            library.save(SOURCE, confirmed=False)
        first = library.save(SOURCE, confirmed=True)
        identity = first.definition.id
        store.put("chart_indicators", "main", {"version": 2, "items": [{"definition": identity, "definition_revision": 1}]})
        store.put("rule", "rule", {"conditions": [{"indicator": {"definition": identity}}]}, "demo:test")
        store.put("scanner_template", "scan", {"filters": {"conditions": [{"indicator": {"definition": identity}}]}})
        assert library.references(identity) == {"charts": 1, "alerts": 1, "strategies": 0, "workspaces": 0, "scanner": 1}
        second = library.save(SOURCE.replace("105", "106"), identity=identity, expected_revision=1, confirmed=True)
        assert second.definition.revision == 2 and catalog.resolve(identity, 1) == first.definition
        with pytest.raises(ValueError, match="版本已改变"):
            library.save(SOURCE, identity=identity, expected_revision=1, confirmed=True)
        with pytest.raises(ValueError, match="仍被引用"):
            library.delete(identity, confirmed=True)
        library.hide(identity)
        restored = IndicatorLibrary(store, IndicatorCatalog())
        restored.load()
        assert not restored.errors and restored.source(identity, 1) == SOURCE
        assert identity not in {item.id for item in restored.catalog.list()}
        assert restored.catalog.resolve(identity, 1) == first.definition
        record = store.get("indicator_definition", identity + "/2")
        store.put("indicator_definition", identity + "/2", dict(record, source=SOURCE + "\n// changed"))
        corrupt = IndicatorLibrary(store, IndicatorCatalog())
        corrupt.load()
        assert identity + "/2" in corrupt.errors
        assert corrupt.catalog.resolve(identity, 1) == first.definition
    finally:
        store.close()


def test_custom_boolean_alert_and_scanner_use_confirmed_current_values() -> None:
    catalog, instance = catalog_instance()
    condition = {"metric": "indicator", "bar": "1m", "indicator": spec_record(instance), "line": "buy", "op": "is_true"}
    rule = validate_rule({"name": "signal", "instrument": "BTC-USDT-SWAP", "mode": "all", "conditions": [condition],
                          "cooldown": 0}, catalog=catalog)
    engine = AlertEngine(catalog=catalog)
    rows = candles()
    pair = "BTC-USDT-SWAP", "1m"
    def check(count: int):
        now = rows[count-1][0]/1000+60
        return engine.evaluate("rule", rule, {"price": "110", "source": "okx", "time": now}, [],
                               {pair: rows[:count]}, now)
    assert check(2) is None  # false 建立基线
    assert check(3) is not None
    assert check(3) is None
    assert check(4) is None
    assert check(5) is not None
    registry = IndicatorRegistry(catalog=catalog)
    assert indicator_match(rows[:2], condition, registry.get, pair, catalog=catalog) is False
    assert indicator_match(rows[:3], condition, registry.get, pair, catalog=catalog) is True
    unconfirmed = list(rows[2]); unconfirmed[8] = 0
    assert indicator_match(CandleBuffer([*rows[:2], unconfirmed]), condition, registry.get, pair, catalog=catalog) is False
    assert compare_values("is_true", None, None) is None
    assert compare_values("is_false", None, False) is True
    with pytest.raises(ValueError, match="布尔输出"):
        validate_condition(dict(condition, op="above", threshold=0), catalog)
    now = rows[4][0]/1000+10000
    assert engine.evaluate("rule", rule, {"price": "110", "source": "okx", "time": now}, [], {pair: rows[:5]}, now) is None


def test_custom_backtest_executes_after_signal_and_does_not_read_future() -> None:
    catalog, instance = catalog_instance()
    strategy = {"bar": "1m", "direction": "long", "stop_percent": "1", "target_percent": "1", "risk_percent": "1",
                "conditions": [{"metric": "indicator", "indicator": spec_record(instance), "line": "buy", "op": "is_true"}]}
    rows = candles()
    spec = {"ctType": "linear", "settleCcy": "USDT", "ctVal": "1", "lotSz": "1", "minSz": "1"}
    result = run_backtest(rows, strategy, spec, catalog=catalog)
    assert result["trades"] and result["trades"][0]["entry_time"] == rows[3][0]
    before = evaluate(instance, rows[:3], bar="1m", catalog=catalog)
    after = evaluate(instance, rows, bar="1m", catalog=catalog)
    assert list(before.outputs["buy"]) == list(after.outputs["buy"][:3])
    assert not run_backtest(rows[:3], strategy, spec, catalog=catalog)["trades"]


def test_custom_replay_signals_stay_before_cursor_and_in_replay_scope(tmp_path: Path) -> None:
    catalog, instance = catalog_instance()
    store = Store(tmp_path / "replay.sqlite3")
    rule = {"name": "signal", "instrument": "BTC-USDT-SWAP", "mode": "all", "cooldown": 0,
            "conditions": [{"metric": "indicator", "bar": "1m", "indicator": spec_record(instance), "line": "buy", "op": "is_true"}]}
    session = ReplaySession(store, {}, "BTC-USDT-SWAP", "1m", candles(), catalog=catalog)
    try:
        assert not session.evaluate_rules([("r", rule)])
        assert session.step() and not session.evaluate_rules([("r", rule)])
        assert session.step()
        events = session.evaluate_rules([("r", rule)])
        assert len(events) == 1 and events[0]["source"] == "replay"
        assert max(session.alert_engine.indicator_registry.items.values(), key=lambda stream: len(stream.rows)).rows[-1][0] == session.visible()[-1][0]
        assert len(store.list("replay_alert", session.scope)) == 1
        assert not store.list("event", "demo:test")
    finally:
        session.close()
        store.close()


def test_condition_dialog_reads_custom_inputs_boolean_outputs_and_pinned_revision(app: QApplication) -> None:
    catalog, instance = catalog_instance()
    catalog.register_program(compile_source(SOURCE.replace("105", "106"), identity="custom.signal", revision=2))
    condition = {"metric": "indicator", "indicator": spec_record(instance), "line": "buy", "op": "is_false", "bar": "1m"}
    dialog = IndicatorConditionDialog(condition, catalog=catalog)
    try:
        assert dialog.kind.currentData() == "custom.signal/1"
        assert not dialog.threshold.isEnabled() and not dialog.compare_enabled.isEnabled()
        assert dialog.input_fields.values()["enabled"] is True
        dialog.save()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.condition is not None and "threshold" not in dialog.condition
        validate_condition(dialog.condition, catalog)
    finally:
        dialog.deleteLater()
