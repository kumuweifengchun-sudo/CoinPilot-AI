"""规则只使用当前 OKX 数值，AI 不参与触发决策。"""
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from .models import instrument_id, number
from coinpilot_ai.market.intervals import BARS as BAR_SECONDS
from coinpilot_ai.market.indicators import calculate, validate_spec

METRICS = {"price": "最新价格", "change_pct": "区间涨跌幅 (%)", "volume_ratio": "成交量倍数",
           "upl": "持仓未实现盈亏 (USDT)", "upl_pct": "持仓收益率 (%)",
           "funding": "资金费率", "oi": "持仓量", "oi_change_pct": "持仓量变化 (%)",
           "mark": "标记价格", "index": "指数价格", "basis": "基差 (%)"}
BARS = ("1m", "5m", "15m", "1H", "4H", "1D")


def validate_rule(rule):
    rule = dict(rule)
    rule["instrument"] = instrument_id(rule["instrument"])
    if not rule.get("name", "").strip():
        raise ValueError("请填写提醒名称")
    if rule.get("mode") not in ("all", "any", "order"):
        raise ValueError("无效的组合方式")
    cooldown = int(rule.get("cooldown", 300))
    if not 0 <= cooldown <= 86400:
        raise ValueError("冷却时间需要在 0～86400 秒之间")
    rule["cooldown"] = cooldown
    if rule.get("trigger", "edge") not in ("edge", "once", "bar", "cooldown"):
        raise ValueError("无效的触发模式")
    rule.setdefault("trigger", "edge")
    if rule["trigger"] == "cooldown" and cooldown == 0:
        raise ValueError("持续满足并按冷却触发时，冷却时间须大于零")
    if rule["trigger"] == "bar":
        if rule.get("trigger_bar", "1m") not in BARS:
            raise ValueError("无效的触发 K 线周期")
        rule.setdefault("trigger_bar", "1m")
    if rule["mode"] == "order":
        if not set(rule.get("states", [])) <= {"filled", "partially_filled", "canceled", "failed"} or not rule.get("states"):
            raise ValueError("请选择订单事件类型")
        return rule
    if not 1 <= len(rule.get("conditions", [])) <= 8:
        raise ValueError("每条规则需要 1～8 个条件")
    for condition in rule["conditions"]:
        if condition.get("metric") == "indicator":
            spec = validate_spec(condition.get("indicator"))
            if condition.get("bar", "15m") not in BARS:
                raise ValueError("无效的指标周期")
            if condition.get("op") not in ("above", "below", "cross_above", "cross_below"):
                raise ValueError("无效的指标比较方式")
            if "compare" in condition:
                validate_spec(condition["compare"]["indicator"])
            else:
                number(condition["threshold"])
            condition["indicator"] = spec
            continue
        if condition.get("metric") not in METRICS or condition.get("op") not in ("above", "below"):
            raise ValueError("无效的规则条件")
        number(condition["threshold"])
        if condition["metric"] in ("change_pct", "oi_change_pct") and not 60 <= int(condition.get("window", 900)) <= 86400:
            raise ValueError("涨跌幅窗口需要在 60～86400 秒之间")
        if condition["metric"] == "volume_ratio" and condition.get("bar", "15m") not in BARS:
            raise ValueError("无效的成交量周期")
    return rule


@dataclass
class RuleState:
    active: bool | None = None
    last_fired: float = 0
    last_bar: str = ""
    fired_once: bool = False


class AlertEngine:
    def __init__(self):
        self.states = {}
        self.history = {}
        self.last_tick = {}

    def invalidate(self, instrument=None):
        for state in self.states.values():
            state.active = None
        if instrument is None:
            self.history.clear()
            self.last_tick.clear()
        else:
            self.history.pop(instrument, None)
            self.last_tick.pop(instrument, None)

    def tick(self, instrument, price, now, *, max_gap=30):
        history = self.history.setdefault(instrument, deque())
        if history and (now - history[-1][0] > max_gap or now < history[-1][0]):
            history.clear()
            for state in self.states.values():
                state.active = None
        price = number(price, positive=True)
        if not history or now - history[-1][0] >= 1:
            history.append((now, price))
        else:
            history[-1] = (history[-1][0], price)
        while history and now - history[0][0] > 86430:
            history.popleft()
        self.last_tick[instrument] = now

    def metric(self, condition, instrument, quote, positions, candles, now, account_fresh, derivatives=None):
        metric = condition["metric"]
        if metric in ("funding", "oi", "oi_change_pct", "mark", "index", "basis"):
            return derivatives.metric(instrument, metric, now, int(condition.get("window", 900))) if derivatives else None
        if metric == "indicator":
            bar = condition.get("bar", "15m")
            rows = candles.get((instrument, bar), [])
            complete = [row for row in rows if len(row) > 8 and str(row[8]) == "1"]
            if len(complete) < 2:
                return None
            step = BAR_SECONDS[bar]*1000
            start = len(complete)-1
            while start > 0 and int(complete[start][0])-int(complete[start-1][0]) == step:
                start -= 1
            contiguous = complete[start:]
            result = calculate(contiguous, condition["indicator"])
            line = condition.get("line") or next(iter(result.lines))
            values = result.lines.get(line)
            if values is None or len(values) < 2 or values[-1] is None or values[-2] is None:
                return None
            if "compare" in condition:
                other = calculate(contiguous, condition["compare"]["indicator"])
                compare_line = condition["compare"].get("line") or next(iter(other.lines))
                rhs = other.lines.get(compare_line)
                if rhs is None or rhs[-1] is None or rhs[-2] is None:
                    return None
                return (values[-2], values[-1], rhs[-2], rhs[-1], str(result.times[-1]))
            threshold = float(number(condition["threshold"]))
            return (values[-2], values[-1], threshold, threshold, str(result.times[-1]))
        if metric == "price":
            return number(quote["price"])
        if metric == "change_pct":
            target = now - int(condition.get("window", 900))
            history = self.history.get(instrument, [])
            base = next((item for item in reversed(history) if item[0] <= target), None)
            if base is None or target - base[0] > 30:
                return None
            return (number(quote["price"]) / base[1] - 1) * 100
        if metric == "volume_ratio":
            rows = candles.get((instrument, condition.get("bar", "15m")), [])
            complete = [row for row in rows if len(row) > 8 and row[8] == "1"]
            if len(complete) < 21:
                return None
            step = BAR_SECONDS[condition.get("bar", "15m")] * 1000
            tail = complete[-21:]
            if any(int(b[0])-int(a[0]) != step for a, b in zip(tail, tail[1:])):
                return None
            baseline = sum(number(row[5]) for row in complete[-21:-1]) / 20
            return number(complete[-1][5]) / baseline if baseline > 0 else None
        if not account_fresh:
            return None
        matching = [p for p in positions if p.get("instId") == instrument and number(p.get("pos", "0"))]
        if not matching:
            return None
        if metric == "upl":
            return sum(number(p.get("upl") or "0") for p in matching)
        margin = sum(number(p.get("margin") or p.get("imr") or "0") for p in matching)
        return sum(number(p.get("upl") or "0") for p in matching) / margin * 100 if margin > 0 else None

    def evaluate(self, key, rule, quote, positions, candles, now, account_fresh=True, derivatives=None):
        if not rule.get("enabled", True) or rule["mode"] == "order":
            return None
        state = self.states.setdefault(key, RuleState())
        if not quote or quote.get("source") != "okx" or not -5 <= now - quote["time"] <= 30:
            state.active = None
            return None
        conditions, evidence = [], []
        for condition in rule["conditions"]:
            if condition["metric"] in ("indicator", "volume_ratio"):
                bar = condition.get("bar", "15m")
                rows = candles.get((rule["instrument"], bar), [])
                closed = next((row for row in reversed(rows) if len(row) > 8 and str(row[8]) == "1"), None)
                if closed is None or not -5 <= now-(int(closed[0])/1000+BAR_SECONDS[bar]) <= BAR_SECONDS[bar]+30:
                    state.active = None
                    return None
            value = self.metric(condition, rule["instrument"], quote, positions, candles, now, account_fresh, derivatives)
            if value is None:
                state.active = None
                return None
            if condition["metric"] == "indicator":
                left_previous, left, right_previous, right, stamp = value
                op = condition["op"]
                conditions.append({"above": left >= right, "below": left <= right,
                                   "cross_above": left_previous <= right_previous and left > right,
                                   "cross_below": left_previous >= right_previous and left < right}[op])
                evidence.append(dict(condition, value=str(left), compared_to=str(right), candle=stamp))
            else:
                threshold = number(condition["threshold"])
                conditions.append(value >= threshold if condition["op"] == "above" else value <= threshold)
                evidence.append(dict(condition, value=str(value)))
        active = all(conditions) if rule["mode"] == "all" else any(conditions)
        previous, state.active = state.active, active
        marker = "/".join(e.get("candle", "") for e in evidence if e.get("candle"))
        trigger = rule.get("trigger", "edge")
        if trigger == "bar" and not marker:
            rows = candles.get((rule["instrument"], rule.get("trigger_bar", "1m")), [])
            complete = [row for row in rows if len(row) > 8 and str(row[8]) == "1"]
            if not complete:
                state.active = None
                return None
            if not -5 <= now-(int(complete[-1][0])/1000+BAR_SECONDS[rule.get("trigger_bar", "1m")]) <= BAR_SECONDS[rule.get("trigger_bar", "1m")]+30:
                state.active = None
                return None
            marker = str(complete[-1][0])
        eligible = (previous is False if trigger == "edge" else
                    previous is not None and not state.fired_once if trigger == "once" else
                    previous is not None and marker != state.last_bar and bool(marker) if trigger == "bar" else
                    previous is not None)
        if active and eligible and now - state.last_fired >= rule["cooldown"]:
            state.last_fired = now
            state.last_bar = marker
            state.fired_once = True
            return {"rule_id": key, "name": rule["name"], "instrument": rule["instrument"],
                    "time": now, "source": "okx", "evidence": evidence, "price": quote["price"],
                    "priority": "position" if any(c["metric"].startswith("upl") for c in rule["conditions"]) else "market"}
        return None
