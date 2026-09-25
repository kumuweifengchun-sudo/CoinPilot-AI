"""固定价格路径验证指标、提醒与合约仓位边界。"""
from decimal import Decimal

from coinpilot_ai.trading.alerts import AlertEngine, validate_rule
from coinpilot_ai.market.indicators import calculate
from coinpilot_ai.trading.risk import position_plan


def candles(closes, *, confirmed=True, bar_ms=900000):
    return [[str((i+1)*bar_ms), str(close), str(close+1), str(close-1), str(close), "10", "0", "0",
             "1" if confirmed else "0"] for i, close in enumerate(closes)]


def test_indicator_known_values_and_warmup():
    rows = candles([1, 2, 3, 4, 5])
    ma = calculate(rows, {"kind": "MA", "params": {"period": 3}})
    assert ma.lines["value"] == (None, None, 2, 3, 4)
    boll = calculate(rows, {"kind": "BOLL", "params": {"period": 3, "deviations": 2}})
    assert boll.lines["middle"][-1] == 4
    assert boll.lines["upper"][-1] > 5 > boll.lines["lower"][-1]
    rsi = calculate(candles(range(1, 20)), {"kind": "RSI", "params": {"period": 14}})
    assert rsi.lines["value"][13] is None
    assert rsi.lines["value"][14] == 100
    assert calculate(candles([5]*40), {"kind": "MACD"}).lines["histogram"][-1] == 0


def test_indicator_gap_restarts_warmup_and_exposes_validity():
    rows = candles([1, 2, 3, 4, 5, 6])
    rows[3][0] = str(5*900000)
    rows[4][0] = str(6*900000)
    rows[5][0] = str(7*900000)
    result = calculate(rows, {"kind": "MA", "params": {"period": 3}}, bar="15m")
    assert result.lines["value"] == (None, None, 2, None, None, 5)
    assert result.valid["value"] == (False, False, True, False, False, True)
    assert result.warming["value"][3]


def test_indicator_alert_uses_closed_bar_and_cross_once():
    inst = "BTC-USDT-SWAP"
    rule = validate_rule({"name": "交叉", "instrument": inst, "mode": "all", "cooldown": 0,
                          "conditions": [{"metric": "indicator", "indicator": {"kind": "MA", "params": {"period": 2}},
                                          "bar": "15m", "op": "cross_above", "threshold": "1.5"}]})
    engine = AlertEngine()
    quote = {"source": "okx", "price": "3", "time": 4501}
    base = {(inst, "15m"): candles([1, 1, 1])}
    assert engine.evaluate("rule", rule, quote, [], base, 4501) is None
    incomplete = candles([1, 1, 1, 3])
    incomplete[-1][8] = "0"
    assert engine.evaluate("rule", rule, quote, [], {(inst, "15m"): incomplete}, 4501) is None
    quote["time"] = 4502
    complete = {(inst, "15m"): candles([1, 1, 1, 3])}
    assert engine.evaluate("rule", rule, quote, [], complete, 4502) is not None
    assert engine.evaluate("rule", rule, quote, [], complete, 4503) is None
    quote["time"] = 5432
    assert engine.evaluate("rule", rule, quote, [], complete, 5432) is None


def test_cooldown_mode_requires_baseline_and_repeats_only_after_interval():
    inst = "BTC-USDT-SWAP"
    rule = validate_rule({"name": "持续上涨", "instrument": inst, "mode": "all",
                          "cooldown": 60, "trigger": "cooldown",
                          "conditions": [{"metric": "price", "op": "above", "threshold": "2"}]})
    engine = AlertEngine()
    quote = {"source": "okx", "price": "3", "time": 100}
    assert engine.evaluate("rule", rule, quote, [], {}, 100) is None
    quote["time"] = 101
    assert engine.evaluate("rule", rule, quote, [], {}, 101) is not None
    quote["time"] = 130
    assert engine.evaluate("rule", rule, quote, [], {}, 130) is None
    quote["time"] = 161
    assert engine.evaluate("rule", rule, quote, [], {}, 161) is not None


def test_risk_rounds_down_to_lot_and_rejects_wrong_stop():
    spec = {"instId": "BTC-USDT-SWAP", "ctType": "linear", "settleCcy": "USDT",
            "ctVal": "0.01", "ctValCcy": "BTC", "lotSz": "0.1", "minSz": "0.1"}
    plan = position_plan(equity="10000", risk_percent="1", entry="67200", stop="66500",
                         target="68950", direction="long", spec=spec)
    assert plan["contracts"] == Decimal("14.2")
    assert plan["planned_loss"] == Decimal("99.4")
    assert plan["notional"] == Decimal("9542.40")
    assert plan["risk_reward"] == Decimal("2.5")
    try:
        position_plan(equity="10000", risk_percent="1", entry="67200", stop="68000",
                      direction="long", spec=spec)
    except ValueError:
        pass
    else:
        raise AssertionError("做多止损必须低于入场价")
