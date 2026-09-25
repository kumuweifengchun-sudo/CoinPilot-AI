"""不依赖 Qt 的 K 线指标计算，供图表、提醒、扫描和回放共用。"""
from dataclasses import dataclass
from math import sqrt


KINDS = {"MA", "EMA", "RSI", "MACD", "BOLL", "ATR", "VWAP", "VOLUME_MA", "STOCHASTIC", "SUPERTREND"}
DEFAULTS = {"MA": {"period": 20}, "EMA": {"period": 20}, "RSI": {"period": 14},
            "MACD": {"fast": 12, "slow": 26, "signal": 9},
            "BOLL": {"period": 20, "deviations": 2}, "ATR": {"period": 14},
            "VWAP": {}, "VOLUME_MA": {"period": 20},
            "STOCHASTIC": {"period": 14, "smooth": 3},
            "SUPERTREND": {"period": 10, "multiplier": 3}}


@dataclass(frozen=True)
class IndicatorResult:
    kind: str
    times: tuple[int, ...]
    lines: dict[str, tuple[float | None, ...]]
    confirmed: tuple[bool, ...]

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
        from .intervals import BARS
        if bar not in BARS:
            raise ValueError("无效的指标 K 线周期")
        if any(int(b[0]) <= int(a[0]) for a, b in zip(rows, rows[1:])):
            raise ValueError("K 线时间须严格递增")
        step = BARS[bar]*1000
        splits = [0] + [index for index in range(1, len(rows))
                         if int(rows[index][0])-int(rows[index-1][0]) != step] + [len(rows)]
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
