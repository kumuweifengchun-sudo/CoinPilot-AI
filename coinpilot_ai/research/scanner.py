"""OKX USDT 永续市场扫描；请求排队，未知值与不满足明确区分。"""
import time
from collections import deque

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from coinpilot_ai.market.candles import valid_candles
from coinpilot_ai.market.indicators import calculate


TEMPLATES = {
    "放量上涨": {"change_min": 3, "volume_min": 2},
    "超卖": {"rsi_max": 30},
    "突破": {"breakout": True},
    "EMA 金叉": {"ema_cross": True},
    "MACD 金叉": {"macd_cross": True},
    "波动异常": {"atr_pct_min": 3},
}


def parse_tickers(rows, specs, now):
    result = {}
    for row in rows or []:
        inst = row.get("instId")
        if inst not in specs:
            continue
        try:
            price, opened = float(row["last"]), float(row["open24h"])
            stamp = int(row["ts"])/1000
            if price <= 0 or opened <= 0 or not -5 <= now-stamp <= 30:
                continue
            result[inst] = {"price": price, "change24h": (price/opened-1)*100,
                            "time": stamp, "volume24h": float(row.get("vol24h", 0))}
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    return result


def scan_metrics(rows):
    complete = [row for row in rows if len(row) > 8 and str(row[8]) == "1"]
    if len(complete) < 97:
        return None
    step = 900000
    tail = complete[-97:]
    if any(int(b[0])-int(a[0]) != step for a, b in zip(tail, tail[1:])):
        return None
    closes = [float(row[4]) for row in tail]
    volumes = [float(row[5]) for row in tail]
    previous = sum(volumes[-21:-1])/20
    ema_fast = calculate(tail, {"kind": "EMA", "params": {"period": 20}}).lines["value"]
    ema_slow = calculate(tail, {"kind": "EMA", "params": {"period": 60}}).lines["value"]
    macd = calculate(tail, {"kind": "MACD"}).lines
    rsi = calculate(tail, {"kind": "RSI"}).lines["value"][-1]
    atr = calculate(tail, {"kind": "ATR"}).lines["value"][-1]
    return {"change": (closes[-1]/closes[-2]-1)*100,
            "volume_ratio": volumes[-1]/previous if previous else None,
            "rsi": rsi, "breakout": closes[-1] > max(float(row[2]) for row in tail[:-1]),
            "ema_cross": ema_fast[-2] <= ema_slow[-2] and ema_fast[-1] > ema_slow[-1],
            "macd_cross": macd["macd"][-2] <= macd["signal"][-2] and macd["macd"][-1] > macd["signal"][-1],
            "atr_pct": atr/closes[-1]*100 if atr is not None else None,
            "candle": int(tail[-1][0])}


def matches(metrics, filters):
    if metrics is None:
        return None
    checks = []
    for key, metric, op in (("change_min", "change", "min"), ("volume_min", "volume_ratio", "min"),
                            ("rsi_min", "rsi", "min"), ("rsi_max", "rsi", "max"),
                            ("atr_pct_min", "atr_pct", "min")):
        if key in filters:
            value = metrics[metric]
            if value is None:
                return None
            checks.append(value >= float(filters[key]) if op == "min" else value <= float(filters[key]))
    for key in ("breakout", "ema_cross", "macd_cross"):
        if filters.get(key):
            checks.append(bool(metrics[key]))
    return all(checks)


class MarketScanner(QObject):
    changed = pyqtSignal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.tickers = {}
        self.queue = deque()
        self.last_queue = 0
        self.active = False
        self.pending_tickers = False
        self.inflight = set()
        self.backoff = {}
        self.failures = {}
        self.metric_cache = {}
        self.generation = 0
        self.known_specs_count = 0
        self.timer = QTimer(self)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.tick)
        service.updated.connect(self.service_updated)

    def start(self):
        if self.active:
            return
        self.active = True
        self.last_queue = 0
        self.pending_tickers = False
        self.timer.start()
        self.tick()

    def stop(self):
        self.active = False
        self.generation += 1
        self.inflight.clear()
        self.pending_tickers = False
        self.timer.stop()

    def service_updated(self, kind):
        if self.active and kind in ("candles", "market"):
            self.changed.emit()
        elif self.active and kind in ("environment", "proxy"):
            self.generation += 1
            self.inflight.clear()
            self.queue = deque(sorted(self.service.specs))
            self.metric_cache.clear()
            self.pending_tickers = False
            self.last_tickers = 0

    def tick(self):
        if not self.active or self.service.closed:
            return
        now = time.time()
        if len(self.service.specs) != self.known_specs_count:
            self.known_specs_count = len(self.service.specs)
            self.last_queue = 0
            self.last_tickers = 0
        if not self.pending_tickers and now-getattr(self, "last_tickers", 0) >= 30:
            self.pending_tickers = True
            self.last_tickers = now
            generation = self.generation
            self.service.api.get("/api/v5/market/tickers",
                                 lambda rows, error: self.received_tickers(rows, error, generation),
                                 {"instType": "SWAP"})
        if now-self.last_queue >= 300:
            self.last_queue = now
            self.queue = deque(sorted(self.service.specs))
        if self.queue and len(self.inflight) < 3 and len(self.service.chart_feed.pending) < 4:
            inst = self.queue.popleft()
            pair = (inst, "15m")
            if now < self.backoff.get(pair, 0):
                self.queue.append(inst)
            elif now-self.service.candle_times.get(pair, 0) >= 240:
                self.inflight.add(pair)
                generation = self.generation
                self.service.api.get("/api/v5/market/candles",
                    lambda rows, error, p=pair, g=generation: self.received_candles(p, rows, error, g),
                    {"instId": inst, "bar": "15m", "limit": "300"})

    def received_candles(self, pair, rows, error, generation):
        if generation != self.generation or not self.active or self.service.closed:
            return
        self.inflight.discard(pair)
        try:
            if error:
                raise ValueError(str(error))
            rows = valid_candles(rows or [])
            if not rows:
                raise ValueError("暂无 K 线")
            self.failures.pop(pair, None)
            self.backoff.pop(pair, None)
            self.service.chart_feed.merge(pair, rows, fresh=True)
        except (ValueError, TypeError, KeyError, OverflowError):
            count = min(self.failures.get(pair, 0)+1, 6)
            self.failures[pair] = count
            self.backoff[pair] = time.time()+min(300, 10*2**(count-1))
            if pair[0] not in self.queue:
                self.queue.append(pair[0])
            self.changed.emit()

    def received_tickers(self, rows, error, generation):
        if generation != self.generation or not self.active:
            return
        self.pending_tickers = False
        if error or self.service.closed:
            return
        self.tickers = parse_tickers(rows, self.service.specs, time.time())
        self.changed.emit()

    def results(self, filters):
        now = time.time()
        result = []
        for inst in sorted(self.service.specs):
            ticker = self.tickers.get(inst)
            pair = (inst, "15m")
            stamp = self.service.candle_times.get(pair, 0)
            metrics = None
            if now-stamp <= 300:
                revision = self.service.chart_feed.revisions.get(pair, 0)
                cached = self.metric_cache.get(pair)
                if cached is None or cached[0] != revision:
                    cached = (revision, scan_metrics(self.service.candles.get(pair, [])))
                    self.metric_cache[pair] = cached
                metrics = cached[1]
                if metrics is not None and not -5 <= now-(metrics["candle"]/1000+900) <= 930:
                    metrics = None
            pending = pair in self.inflight or inst in self.queue or now < self.backoff.get(pair, 0)
            state = ("就绪" if metrics is not None else "计算中" if pending else
                     "过期" if stamp else "不可用")
            if ticker is None or now-ticker["time"] > 30:
                state = "行情过期"
            accepted = matches(metrics, filters) if state == "就绪" else None
            if accepted is False:
                continue
            result.append((inst, ticker, metrics, state, accepted))
        result.sort(key=lambda row: (row[4] is not True, -abs(row[2]["change"]) if row[2] else 0, row[0]))
        return result
