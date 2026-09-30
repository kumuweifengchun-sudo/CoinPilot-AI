"""不依赖 Qt 的 K 线指标计算，供图表、提醒、扫描和回放共用。"""
from .intervals import contiguous
from dataclasses import dataclass
from array import array
from math import isnan
from collections.abc import Sequence, Mapping
from typing import overload
from math import sqrt
from .rolling import RollingMoments, RollingExtreme


KINDS = {"MA", "EMA", "RSI", "MACD", "BOLL", "ATR", "VWAP", "VOLUME_MA", "STOCHASTIC", "SUPERTREND"}
DEFAULTS = {"MA": {"period": 20}, "EMA": {"period": 20}, "RSI": {"period": 14},
            "MACD": {"fast": 12, "slow": 26, "signal": 9},
            "BOLL": {"period": 20, "deviations": 2}, "ATR": {"period": 14},
            "VWAP": {}, "VOLUME_MA": {"period": 20},
            "STOCHASTIC": {"period": 14, "smooth": 3},
            "SUPERTREND": {"period": 10, "multiplier": 3}}


class NumericLine(Sequence[float | None]):
    """双精度连续存储，以 NaN 表示未预热，读取仍暴露 None。"""
    def __init__(self):
        self.data = array("d")

    def __len__(self) -> int:
        return len(self.data)

    @overload
    def __getitem__(self, index: int) -> float | None: ...

    @overload
    def __getitem__(self, index: slice) -> list[float | None]: ...

    def __getitem__(self, index: int | slice) -> float | None | list[float | None]:
        if isinstance(index, slice):
            return [None if isnan(value) else value for value in self.data[index]]
        value = self.data[index]
        return None if isnan(value) else value

    def __delitem__(self, index):
        del self.data[index]

    def append(self, value):
        self.data.append(float("nan") if value is None else value)


@dataclass(frozen=True)
class IndicatorResult:
    kind: str
    times: Sequence[int]
    lines: Mapping[str, Sequence[float | None]]
    confirmed: Sequence[bool | int]

    @property
    def valid(self):
        return {name: tuple(value is not None for value in values)
                for name, values in self.lines.items()}

    @property
    def warming(self):
        return {name: tuple(value is None for value in values)
                for name, values in self.lines.items()}

    def latest(self, line="value", *, closed=True):
        values = self.lines[line]
        for index in range(len(values)-1, -1, -1):
            if values[index] is not None and (not closed or self.confirmed[index]):
                return self.times[index], values[index]
        return None


def validate_spec(spec):
    if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
        raise ValueError("不支持的指标")
    kind = spec["kind"]
    result = dict(DEFAULTS[kind])
    result.update(spec.get("params", {}))
    for key, value in result.items():
        if key in ("deviations", "multiplier"):
            value = float(value)
            if not 0 < value <= 100:
                raise ValueError("指标倍数须大于 0 且不超过 100")
        else:
            value = int(value)
            if not 1 <= value <= 1000:
                raise ValueError("指标周期须在 1—1000 之间")
        result[key] = value
    if kind == "MACD" and result["fast"] >= result["slow"]:
        raise ValueError("MACD 快线周期须小于慢线周期")
    return {"kind": kind, "params": result}


def _ema(values, period, *, warm=True):
    output, previous, count = [], None, 0
    alpha = 2 / (period + 1)
    for value in values:
        if value is None:
            output.append(None)
            continue
        count += 1
        previous = value if previous is None else previous + alpha * (value - previous)
        output.append(previous if not warm or count >= period else None)
    return output


def _sma(values, period):
    result, window = [], []
    for value in values:
        window.append(value)
        if len(window) > period:
            window.pop(0)
        result.append(sum(window) / period if len(window) == period and all(v is not None for v in window) else None)
    return result


def _wilder(values, period):
    result, seed, previous = [], [], None
    for value in values:
        if value is None:
            result.append(None)
            continue
        if previous is None:
            seed.append(value)
            if len(seed) == period:
                previous = sum(seed) / period
        else:
            previous = (previous * (period-1) + value) / period
        result.append(previous)
    return result


def calculate(rows, spec, *, bar=None):
    spec = validate_spec(spec)
    if bar is not None:
        from .intervals import BARS, contiguous
        if bar not in BARS:
            raise ValueError("无效的指标 K 线周期")
        if any(int(b[0]) <= int(a[0]) for a, b in zip(rows, rows[1:])):
            raise ValueError("K 线时间须严格递增")
        splits = [0] + [index for index in range(1, len(rows))
                         if not contiguous(rows[index-1][0], rows[index][0], bar) or int(rows[index-1][8]) != 1] + [len(rows)]
        if len(splits) > 2:
            pieces = [calculate(rows[start:end], spec) for start, end in zip(splits, splits[1:])]
            names = pieces[0].lines
            return IndicatorResult(spec["kind"], tuple(time for piece in pieces for time in piece.times),
                {name: tuple(value for piece in pieces for value in piece.lines[name]) for name in names},
                tuple(value for piece in pieces for value in piece.confirmed))
    kind, params = spec["kind"], spec["params"]
    times = tuple(int(row[0]) for row in rows)
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("K 线时间须严格递增")
    highs, lows, closes, volumes = [], [], [], []
    for row in rows:
        highs.append(float(row[2]))
        lows.append(float(row[3]))
        closes.append(float(row[4]))
        volumes.append(float(row[5]))
    confirmed = tuple(len(row) > 8 and str(row[8]) == "1" for row in rows)
    if kind in ("MA", "VOLUME_MA"):
        lines = {"value": _sma(volumes if kind == "VOLUME_MA" else closes, params["period"])}
    elif kind == "EMA":
        lines = {"value": _ema(closes, params["period"])}
    elif kind == "RSI":
        changes = [None] + [b-a for a, b in zip(closes, closes[1:])]
        gains = _wilder([max(v, 0) if v is not None else None for v in changes], params["period"])
        losses = _wilder([max(-v, 0) if v is not None else None for v in changes], params["period"])
        lines = {"value": [None if g is None or l is None else 100 if l == 0 else 100-100/(1+g/l)
                           for g, l in zip(gains, losses)]}
    elif kind == "MACD":
        fast = _ema(closes, params["fast"])
        slow = _ema(closes, params["slow"])
        macd = [a-b if a is not None and b is not None else None for a, b in zip(fast, slow)]
        signal = _ema(macd, params["signal"])
        lines = {"macd": macd, "signal": signal,
                 "histogram": [a-b if a is not None and b is not None else None for a, b in zip(macd, signal)]}
    elif kind == "BOLL":
        center = _sma(closes, params["period"])
        upper, lower = [], []
        for i, mean in enumerate(center):
            if mean is None:
                upper.append(None)
                lower.append(None)
            else:
                window = closes[i-params["period"]+1:i+1]
                deviation = sqrt(sum((v-mean)**2 for v in window) / len(window)) * params["deviations"]
                upper.append(mean+deviation)
                lower.append(mean-deviation)
        lines = {"middle": center, "upper": upper, "lower": lower}
    elif kind in ("ATR", "SUPERTREND"):
        ranges = [highs[0]-lows[0]] if rows else []
        ranges += [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
                   for i in range(1, len(rows))]
        atr = _wilder(ranges, params["period"])
        if kind == "ATR":
            lines = {"value": atr}
        else:
            trend, upper, lower, direction = [], None, None, 1
            for i, value in enumerate(atr):
                if value is None:
                    trend.append(None)
                    continue
                midpoint = (highs[i]+lows[i])/2
                new_upper = midpoint+params["multiplier"]*value
                new_lower = midpoint-params["multiplier"]*value
                upper = new_upper if upper is None or closes[i-1] > upper else min(new_upper, upper)
                lower = new_lower if lower is None or closes[i-1] < lower else max(new_lower, lower)
                if closes[i] > upper:
                    direction = 1
                elif closes[i] < lower:
                    direction = -1
                trend.append(lower if direction > 0 else upper)
            lines = {"value": trend}
    elif kind == "VWAP":
        numerator = denominator = 0.0
        values = []
        session = None
        for stamp, high, low, close, volume in zip(times, highs, lows, closes, volumes):
            day = stamp // 86400000
            if day != session:
                numerator = denominator = 0.0
                session = day
            numerator += (high+low+close)/3*volume
            denominator += volume
            values.append(numerator/denominator if denominator else None)
        lines = {"value": values}
    else:  # STOCHASTIC
        raw = []
        for i, close in enumerate(closes):
            if i+1 < params["period"]:
                raw.append(None)
                continue
            high = max(highs[i-params["period"]+1:i+1])
            low = min(lows[i-params["period"]+1:i+1])
            raw.append(50.0 if high == low else (close-low)/(high-low)*100)
        k = _sma(raw, params["smooth"])
        lines = {"k": k, "d": _sma(k, params["smooth"])}
    return IndicatorResult(kind, times, {key: tuple(value) for key, value in lines.items()}, confirmed)



class IndicatorState:
    """后台构建后由 Qt 线程持有的增量状态，结果缓冲区供消费方只读。

    普通追加/末根修正不复制历史结果；递归状态保留末根之前的检查点。
    更早修正、参数或历史结构变化时重建；有限窗口使用可回退的滚动统计。
    """
    RECURSIVE = {"EMA", "RSI", "MACD", "ATR", "SUPERTREND", "VWAP"}

    def __init__(self, spec, *, bar=None):
        self.spec = validate_spec(spec)
        self.kind, self.params = self.spec["kind"], self.spec["params"]
        from .intervals import BARS, contiguous
        self.step = BARS[bar]*1000 if bar is not None else None
        self.bar = bar
        self.state = {}
        self.before_last = {}
        self.windows, self.before_windows = {}, {}
        self.times, self.confirmed, self.lines = array("q"), array("B"), {}
        self.result = IndicatorResult(self.kind, self.times, self.lines, self.confirmed)
        self.processed_rows = 0

    def _average(self, name, value, period, *, wilder=False):
        if value is None:
            return None
        count, total, previous = self.state.get(name, (0, 0.0, None))
        count += 1
        if wilder:
            if previous is None:
                total += value
                if count == period:
                    previous = total/period
            else:
                previous = (previous*(period-1)+value)/period
        else:
            previous = value if previous is None else previous+2/(period+1)*(value-previous)
        self.state[name] = (count, total, previous)
        return previous if count >= period else None

    def _next(self, row):
        stamp = int(row[0])
        high, low, close, volume = (float(row[i]) for i in (2, 3, 4, 5))
        previous_stamp = self.state.get("stamp")
        if previous_stamp is not None and stamp <= previous_stamp:
            raise ValueError("K 线时间须严格递增")
        if (self.step is not None and previous_stamp is not None and
                (not contiguous(previous_stamp, stamp, self.bar) or not self.state.get("confirmed", True))):
            self.state = {}
            self.windows = {}
        previous_close = self.state.get("close")
        p, kind = self.params, self.kind
        if kind == "EMA":
            values = {"value": self._average("ema", close, p["period"])}
        elif kind == "RSI":
            change = close-previous_close if previous_close is not None else None
            gain = self._average("gain", max(change, 0) if change is not None else None, p["period"], wilder=True)
            loss = self._average("loss", max(-change, 0) if change is not None else None, p["period"], wilder=True)
            values = {"value": None if gain is None or loss is None else 100 if loss == 0 else 100-100/(1+gain/loss)}
        elif kind == "MACD":
            fast = self._average("fast", close, p["fast"])
            slow = self._average("slow", close, p["slow"])
            macd = fast-slow if fast is not None and slow is not None else None
            signal = self._average("signal", macd, p["signal"])
            values = {"macd": macd, "signal": signal,
                      "histogram": macd-signal if macd is not None and signal is not None else None}
        elif kind in ("ATR", "SUPERTREND"):
            tr = high-low if previous_close is None else max(high-low, abs(high-previous_close), abs(low-previous_close))
            atr = self._average("atr", tr, p["period"], wilder=True)
            value = atr
            if kind == "SUPERTREND" and atr is not None:
                upper, lower, direction = self.state.get("trend", (None, None, 1))
                middle = (high+low)/2
                new_upper, new_lower = middle+p["multiplier"]*atr, middle-p["multiplier"]*atr
                upper = new_upper if upper is None or previous_close > upper else min(new_upper, upper)
                lower = new_lower if lower is None or previous_close < lower else max(new_lower, lower)
                if close > upper:
                    direction = 1
                elif close < lower:
                    direction = -1
                self.state["trend"] = (upper, lower, direction)
                value = lower if direction > 0 else upper
            values = {"value": value}
        elif kind in ("MA", "VOLUME_MA", "BOLL"):
            window = self.windows.setdefault("value", RollingMoments(p["period"]))
            mean = window.push(volume if kind == "VOLUME_MA" else close)
            values = {"value": mean}
            if kind == "BOLL":
                deviation = sqrt(max(0, window.m2)/p["period"])*p["deviations"] if mean is not None else None
                values = {"middle": mean, "upper": mean+deviation if mean is not None else None,
                          "lower": mean-deviation if mean is not None else None}
        elif kind == "STOCHASTIC":
            highest = self.windows.setdefault("high", RollingExtreme(p["period"], maximum=True)).push(high)
            lowest = self.windows.setdefault("low", RollingExtreme(p["period"], maximum=False)).push(low)
            raw = None if highest is None else 50.0 if highest == lowest else (close-lowest)/(highest-lowest)*100
            k = self.windows.setdefault("k", RollingMoments(p["smooth"])).push(raw)
            d = self.windows.setdefault("d", RollingMoments(p["smooth"])).push(k)
            values = {"k": k, "d": d}
        else:  # VWAP
            day = stamp//86400000
            numerator, denominator = self.state.get("vwap", (0.0, 0.0)) if self.state.get("day") == day else (0.0, 0.0)
            numerator += (high+low+close)/3*volume
            denominator += volume
            self.state["day"], self.state["vwap"] = day, (numerator, denominator)
            values = {"value": numerator/denominator if denominator else None}
        self.state["stamp"], self.state["close"] = stamp, close
        self.state["confirmed"] = int(row[8]) == 1
        return values

    def update(self, rows, start, *, dropped=0):
        if dropped:
            for values in (self.times, self.confirmed, *self.lines.values()):
                del values[:dropped]
        size = len(self.times)
        start = int(start)
        rebuild = (start < max(0, size-1) or start > size or len(rows) < size or
                   (start > 0 and int(rows[start-1][0]) != self.times[start-1]))
        if rebuild or start == 0:
            start = 0
            self.times, self.confirmed, self.lines = array("q"), array("B"), {}
            self.state, self.before_last = {}, {}
            self.windows, self.before_windows = {}, {}
        elif start < size:
            self.state = self.before_last.copy()
            for window in self.windows.values():
                window.undo()
            self.windows = self.before_windows.copy()
        del self.times[start:]
        del self.confirmed[start:]
        for values in self.lines.values():
            del values[start:]
        for i in range(start, len(rows)):
            row = rows[i]
            self.before_last = self.state.copy()
            self.before_windows = self.windows.copy()
            values = self._next(row)
            self.processed_rows += 1
            self.times.append(int(row[0]))
            self.confirmed.append(len(row) > 8 and int(row[8]) == 1)
            for name, value in values.items():
                self.lines.setdefault(name, NumericLine()).append(value)
        if not self.lines:
            self.lines = {name: NumericLine() for name in calculate([], self.spec).lines}
        self.result = IndicatorResult(self.kind, self.times, self.lines, self.confirmed)
        return self.result


class IndicatorStream:
    """逐根输入、有限结果窗口，供扫描/提醒/回测共用。"""
    def __init__(self, spec, bar, limit=2048):
        from .buffer import CandleBuffer
        self.rows = CandleBuffer()
        self.state = IndicatorState(spec, bar=bar)
        self.limit = limit

    def push(self, row):
        from .buffer import candle
        row = candle(row)
        correction = bool(self.rows) and self.rows[-1][0] == int(row[0])
        if correction:
            self.rows[-1] = row
        else:
            self.rows.append(row)
        dropped = len(self.rows)-self.limit+self.limit//4 if len(self.rows) > self.limit else 0
        if dropped:
            del self.rows[:dropped]
        return self.state.update(self.rows, len(self.rows)-1, dropped=dropped)


class IndicatorRegistry:
    """非图表调用方复用最后已收盘状态；每组合/参数只消费新增行。"""
    def __init__(self, limit=128):
        from collections import OrderedDict
        self.items = OrderedDict()
        self.limit = limit

    def get(self, pair, rows, spec):
        from bisect import bisect_left
        normalized = validate_spec(spec)
        key = pair, normalized["kind"], tuple(sorted(normalized["params"].items()))
        stream = self.items.get(key)
        times = rows.times if hasattr(rows, "times") else [int(row[0]) for row in rows]
        start = 0
        if stream is not None and stream.rows:
            stamp = stream.rows[-1][0]
            index = bisect_left(times, stamp)
            if index < len(rows) and int(rows[index][0]) == stamp:
                start = index
            else:
                stream = None
        if stream is None:
            stream = IndicatorStream(normalized, pair[1])
        for index in range(start, len(rows)):
            row = rows[index]
            if int(row[8]) == 1:
                if not stream.rows or tuple(row) != stream.rows[-1]:
                    stream.push(row)
        self.items[key] = stream
        self.items.move_to_end(key)
        while len(self.items) > self.limit:
            self.items.popitem(last=False)
        return stream.state.result
