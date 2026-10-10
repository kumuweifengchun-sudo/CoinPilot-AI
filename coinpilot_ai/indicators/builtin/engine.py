"""保留原内置指标数值与检查点算法，平台通过定义目录选择执行器。"""
from array import array
from math import sqrt
from coinpilot_ai.market.intervals import contiguous
from coinpilot_ai.market.rolling import RollingMoments, RollingExtreme
from coinpilot_ai.indicators.runtime.series import NumericLine
from coinpilot_ai.indicators.runtime.result import IndicatorResult
from coinpilot_ai.indicators.validation import legacy_spec as validate_spec

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


def _reference(rows, spec, *, bar=None):
    spec = validate_spec(spec)
    if bar is not None:
        from coinpilot_ai.market.intervals import BARS, contiguous
        if bar not in BARS:
            raise ValueError("无效的指标 K 线周期")
        if any(int(b[0]) <= int(a[0]) for a, b in zip(rows, rows[1:])):
            raise ValueError("K 线时间须严格递增")
        splits = [0] + [index for index in range(1, len(rows))
                         if not contiguous(rows[index-1][0], rows[index][0], bar) or int(rows[index-1][8]) != 1] + [len(rows)]
        if len(splits) > 2:
            pieces = [_reference(rows[start:end], spec) for start, end in zip(splits, splits[1:])]
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
                window = closes[i-int(params["period"])+1:i+1]
                deviation = sqrt(sum((v-mean)**2 for v in window) / len(window)) * params["deviations"]
                upper.append(mean+deviation)
                lower.append(mean-deviation)
        lines = {"middle": center, "upper": upper, "lower": lower}
    elif kind == "DONCHIAN":
        period = int(params["period"])
        upper = [max(highs[i-period+1:i+1]) if i+1 >= period else None for i in range(len(rows))]
        lower = [min(lows[i-period+1:i+1]) if i+1 >= period else None for i in range(len(rows))]
        lines = {"upper": upper, "middle": [(high+low)/2 if high is not None and low is not None else None
                                           for high, low in zip(upper, lower)], "lower": lower}
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
            high = max(highs[i-int(params["period"])+1:i+1])
            low = min(lows[i-int(params["period"])+1:i+1])
            raw.append(50.0 if high == low else (close-low)/(high-low)*100)
        k = _sma(raw, params["smooth"])
        lines = {"k": k, "d": _sma(k, params["smooth"])}
    return IndicatorResult(kind, times, {key: tuple(value) for key, value in lines.items()}, confirmed)



class _BuiltinState:
    """后台构建后由 Qt 线程持有的增量状态，结果缓冲区供消费方只读。

    普通追加/末根修正不复制历史结果；递归状态保留末根之前的检查点。
    更早修正、参数或历史结构变化时重建；有限窗口使用可回退的滚动统计。
    """
    RECURSIVE = {"EMA", "RSI", "MACD", "ATR", "SUPERTREND", "VWAP"}

    def __init__(self, spec, *, bar=None):
        self.spec = validate_spec(spec)
        self.kind, self.params = self.spec["kind"], self.spec["params"]
        from coinpilot_ai.market.intervals import BARS, contiguous
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
        elif kind == "DONCHIAN":
            highest = self.windows.setdefault("high", RollingExtreme(p["period"], maximum=True)).push(high)
            lowest = self.windows.setdefault("low", RollingExtreme(p["period"], maximum=False)).push(low)
            values = {"upper": highest, "middle": (highest+lowest)/2 if highest is not None and lowest is not None else None,
                      "lower": lowest}
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
            self.lines = {name: NumericLine() for name in _reference([], self.spec).lines}
        self.result = IndicatorResult(self.kind, self.times, self.lines, self.confirmed)
        return self.result
