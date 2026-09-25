"""已收盘指标信号、下一根开盘成交的单持仓策略回测。"""
from decimal import Decimal, ROUND_FLOOR

from coinpilot_ai.market.candles import valid_candles
from coinpilot_ai.market.intervals import BARS
from coinpilot_ai.trading.models import number
from coinpilot_ai.market.indicators import calculate, validate_spec
from coinpilot_ai.trading.matching import execution_fee, execution_price, protective_exit


def validate_strategy(strategy):
    if strategy.get("bar") not in BARS or strategy.get("direction") not in ("long", "short"):
        raise ValueError("策略周期或方向无效")
    if not strategy.get("conditions"):
        raise ValueError("至少设置一个入场条件")
    for condition in strategy["conditions"]:
        if condition.get("metric") != "indicator":
            raise ValueError("策略首版仅支持指标入场条件")
        validate_spec(condition["indicator"])
        if condition.get("op") not in ("above", "below", "cross_above", "cross_below"):
            raise ValueError("指标比较方式无效")
        if "compare" in condition:
            validate_spec(condition["compare"]["indicator"])
        else:
            number(condition["threshold"])
    for field in ("stop_percent", "target_percent", "risk_percent"):
        if not 0 < number(strategy[field], field, positive=True) <= 100:
            raise ValueError(field + " 须在 0—100% 之间")
    return strategy


def _condition_series(rows, condition):
    primary = calculate(rows, condition["indicator"])
    lhs = primary.lines.get(condition.get("line") or next(iter(primary.lines)))
    if lhs is None:
        raise ValueError("指标数值线不存在")
    if "compare" in condition:
        second = calculate(rows, condition["compare"]["indicator"])
        rhs = second.lines.get(condition["compare"].get("line") or next(iter(second.lines)))
        if rhs is None:
            raise ValueError("比较指标数值线不存在")
    else:
        rhs = [float(number(condition["threshold"]))]*len(rows)
    return lhs, rhs


def run_backtest(rows, strategy, spec, *, initial="10000", fee_rate="0.0005", slippage_bps="0"):
    validate_strategy(strategy)
    rows = valid_candles(rows)
    if len(rows) < 2 or any(str(row[8]) != "1" for row in rows):
        raise ValueError("回测需要已收盘 K 线")
    step = BARS[strategy["bar"]]*1000
    if any(int(b[0])-int(a[0]) != step for a, b in zip(rows, rows[1:])):
        raise ValueError("历史 K 线存在缺口，不能回测")
    if spec.get("ctType") != "linear" or spec.get("settleCcy") != "USDT":
        raise ValueError("仅支持 USDT 本位线性永续")
    unit = number(spec["ctVal"], positive=True) * number(spec.get("ctMult") or "1", positive=True)
    lot = number(spec["lotSz"], positive=True)
    minimum = number(spec["minSz"], positive=True)
    equity = number(initial, positive=True)
    fee = number(fee_rate)
    slip = number(slippage_bps)
    if not 0 <= fee <= 1 or not 0 <= slip < 10000:
        raise ValueError("手续费率须在 0—1，滑点须低于 10000 基点")
    signals = [_condition_series(rows, condition) for condition in strategy["conditions"]]
    trades, curve, position = [], [], None
    direction = 1 if strategy["direction"] == "long" else -1
    for index in range(1, len(rows)):
        row = rows[index]
        opened, high, low, closed = [number(row[j]) for j in (1, 2, 3, 4)]
        if position is None:
            checks = []
            for condition, (left, right) in zip(strategy["conditions"], signals):
                previous, current = left[index-2] if index >= 2 else None, left[index-1]
                rhs_previous, rhs_current = right[index-2] if index >= 2 else None, right[index-1]
                if current is None or rhs_current is None:
                    checks.append(False)
                    continue
                op = condition["op"]
                checks.append({"above": current >= rhs_current, "below": current <= rhs_current,
                    "cross_above": previous is not None and rhs_previous is not None and previous <= rhs_previous and current > rhs_current,
                    "cross_below": previous is not None and rhs_previous is not None and previous >= rhs_previous and current < rhs_current}[op])
            if all(checks):
                entry = execution_price(opened, "buy" if direction > 0 else "sell", "market", slip)
                stop_distance = entry * number(strategy["stop_percent"])/100
                size = (equity*number(strategy["risk_percent"])/100 / (stop_distance*unit) / lot).to_integral_value(rounding=ROUND_FLOOR)*lot
                if size >= minimum:
                    position = {"entry_time": int(row[0]), "entry": entry, "size": size,
                                "stop": entry-direction*stop_distance,
                                "target": entry+direction*entry*number(strategy["target_percent"])/100,
                                "direction": strategy["direction"]}
        if position is not None:
            exit_plan = protective_exit(opened, high, low, position["stop"], position["target"],
                                        long=direction > 0)
            if exit_plan is not None:
                raw_exit, cause, ambiguous = exit_plan
                exit_price = execution_price(raw_exit, "sell" if direction > 0 else "buy",
                                             "market" if cause == "stop" else "limit", slip)
                gross = (exit_price-position["entry"])*direction*position["size"]*unit
                costs = (execution_fee(position["entry"], position["size"], unit, fee) +
                         execution_fee(exit_price, position["size"], unit, fee))
                net = gross-costs
                equity += net
                trades.append({**position, "exit_time": int(row[0]), "exit": exit_price,
                               "gross": gross, "fees": costs, "net": net, "cause": cause,
                               "ambiguous": ambiguous})
                position = None
        curve.append({"time": int(row[0]), "equity": equity})
    gains = sum((max(trade["net"], 0) for trade in trades), Decimal(0))
    losses = sum((max(-trade["net"], 0) for trade in trades), Decimal(0))
    peak = number(initial)
    drawdown = Decimal(0)
    for point in curve:
        peak = max(peak, point["equity"])
        drawdown = max(drawdown, peak-point["equity"])
    return {"trades": trades, "equity_curve": curve, "open_position": position,
            "final_realized_equity": equity, "max_drawdown": drawdown,
            "win_rate": sum(trade["net"] > 0 for trade in trades)/len(trades) if trades else None,
            "profit_factor": gains/losses if losses else None,
            "coverage": {"begin": int(rows[0][0]), "end": int(rows[-1][0]), "bars": len(rows)},
            "assumptions": {"fee_rate": str(fee), "slippage_bps": str(slip),
                            "same_bar_exit": "stop_first", "entry": "next_open"}}
