"""已收盘指标信号、下一根开盘成交的单持仓策略回测。"""
from decimal import Decimal, ROUND_FLOOR

from coinpilot_ai.market.buffer import candle
from coinpilot_ai.market.intervals import BARS, contiguous
from coinpilot_ai.trading.models import number
from coinpilot_ai.market.indicators import IndicatorStream, validate_spec
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


class CurveWindow:
    """按相邻时间桶压缩，保留首尾及桶内权益极值。"""
    def __init__(self, limit=4096):
        self.limit, self.width, self.buckets = limit, 1, []

    def append(self, index, point):
        bucket = index//self.width
        if self.buckets and self.buckets[-1][0] == bucket:
            points = self.buckets[-1][1]+[point]
            self.buckets[-1] = (bucket, self.compact(points))
        else:
            self.buckets.append((bucket, [point]))
        if len(self.buckets)*4 > self.limit:
            self.width *= 2
            merged = []
            for key, points in self.buckets:
                key //= 2
                if merged and merged[-1][0] == key:
                    merged[-1] = (key, self.compact(merged[-1][1]+points))
                else:
                    merged.append((key, points))
            self.buckets = merged

    @staticmethod
    def compact(points):
        choices = (points[0], min(points, key=lambda p: p['equity']),
                   max(points, key=lambda p: p['equity']), points[-1])
        return sorted({p['time']: p for p in choices}.values(), key=lambda p: p['time'])

    def points(self):
        return [point for _, points in self.buckets for point in points]


def run_backtest(rows, strategy, spec, *, initial="10000", fee_rate="0.0005", slippage_bps="0", cancelled=lambda: False):
    validate_strategy(strategy)
    if len(rows) < 2:
        raise ValueError("回测需要已收盘 K 线")
    def checked(row, previous=None):
        value = candle(row)
        if value[8] != 1:
            raise ValueError("回测需要已收盘 K 线")
        if previous is not None and not contiguous(previous[0], value[0], strategy["bar"]):
            raise ValueError("历史 K 线存在缺口，不能回测")
        return value
    first = checked(rows[0])
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
    streams = {}
    def stream(spec):
        normalized = validate_spec(spec)
        key = normalized["kind"], tuple(sorted(normalized["params"].items()))
        if key not in streams:
            streams[key] = IndicatorStream(normalized, strategy["bar"])
            streams[key].push(first)
        return streams[key]
    signals = [(stream(c["indicator"]), stream(c["compare"]["indicator"]) if "compare" in c else None)
               for c in strategy["conditions"]]
    trades, curve, position = [], CurveWindow(), None
    peak, drawdown = equity, Decimal(0)
    previous_row = first
    def pair_values(state, name):
        lines = state.state.result.lines
        values = lines.get(name or next(iter(lines)))
        if values is None:
            raise ValueError("指标数值线不存在")
        return (values[-2] if len(values) >= 2 else None), values[-1]
    direction = 1 if strategy["direction"] == "long" else -1
    for index in range(1, len(rows)):
        if cancelled():
            raise ValueError("回测已取消")
        row = checked(rows[index], previous_row)
        previous_row = row
        opened, high, low, closed = [number(row[j]) for j in (1, 2, 3, 4)]
        if position is None:
            checks = []
            for condition, (left, right) in zip(strategy["conditions"], signals):
                previous, current = pair_values(left, condition.get("line"))
                rhs_previous, rhs_current = (pair_values(right, condition["compare"].get("line"))
                    if right else (float(number(condition["threshold"])),)*2)
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
        curve.append(index, {"time": int(row[0]), "equity": equity})
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
        for state in streams.values():
            state.push(row)
    gains = sum((max(trade["net"], 0) for trade in trades), Decimal(0))
    losses = sum((max(-trade["net"], 0) for trade in trades), Decimal(0))
    return {"trades": trades, "equity_curve": curve.points(), "open_position": position,
            "final_realized_equity": equity, "max_drawdown": drawdown,
            "win_rate": sum(trade["net"] > 0 for trade in trades)/len(trades) if trades else None,
            "profit_factor": gains/losses if losses else None,
            "coverage": {"begin": int(rows[0][0]), "end": int(rows[-1][0]), "bars": len(rows)},
            "assumptions": {"fee_rate": str(fee), "slippage_bps": str(slip),
                            "same_bar_exit": "stop_first", "entry": "next_open"}}
