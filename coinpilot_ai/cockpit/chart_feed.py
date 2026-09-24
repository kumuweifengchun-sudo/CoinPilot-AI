"""OKX 公共 K 线：历史分页、REST 校准和业务 WebSocket。

与工作台现有公共报价使用相同的生产行情源；模拟/真实仅隔离账户及图表状态。
"""
import json
import math
import time

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtWebSockets import QWebSocket

from .chart_state import BARS


def valid_candles(rows):
    values = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 9:
            raise ValueError("K 线字段不完整")
        stamp = int(row[0])
        nums = [float(v) for v in row[1:8]]
        if stamp <= 0 or not all(math.isfinite(v) for v in nums) or min(nums[:4]) <= 0 or min(nums[4:]) < 0:
            raise ValueError("K 线数值无效")
        op, high, low, close = nums[:4]
        if low > min(op, close) or high < max(op, close) or low > high or str(row[8]) not in ("0", "1"):
            raise ValueError("K 线价格范围无效")
        values[stamp] = list(row[:9])
    return [values[t] for t in sorted(values)]


class CandleStream(QObject):
    def __init__(self, proxy, receive, status, parent=None, socket_factory=None):
        super().__init__(parent)
        self.proxy, self.receive, self.status = proxy, receive, status
        self.socket_factory = socket_factory or (lambda: QWebSocket(parent=self))
        self.socket = None
        self.pair = None
        self.closed = False
        self.failures = 0
        self.last_message = self.last_ping = 0
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.start)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self.check)

    def select(self, pair):
        if self.pair == pair:
            return
        self.drop()
        self.retry.stop()
        self.pair = pair
        self.failures = 0
        self.start()

    def start(self):
        if self.closed or self.socket is not None or not self.pair:
            return
        socket = self.socket_factory()
        self.socket = socket
        pair = self.pair
        socket.setProxy(self.proxy)
        socket.connected.connect(lambda: self.connected(socket, pair))
        socket.textMessageReceived.connect(lambda msg: self.message(socket, pair, msg))
        socket.disconnected.connect(lambda: self.failed(socket))
        socket.errorOccurred.connect(lambda _: self.failed(socket))
        self.last_message = self.last_ping = time.monotonic()
        self.status("重连中")
        self.watchdog.start()
        socket.open(QUrl("wss://ws.okx.com:8443/ws/v5/business"))

    def connected(self, socket, pair):
        if socket is self.socket:
            socket.sendTextMessage(json.dumps({"op": "subscribe", "args": [{"channel": "candle" + pair[1], "instId": pair[0]}]}))

    def message(self, socket, pair, message):
        if self.closed or socket is not self.socket or pair != self.pair:
            return
        if message == "pong":
            return  # 心跳不能使过期行情变为新鲜。
        try:
            payload = json.loads(message)
            if payload.get("event") == "error":
                self.failed(socket)
                return
            arg = payload.get("arg", {})
            if arg.get("instId") != pair[0] or arg.get("channel") != "candle" + pair[1] or "data" not in payload:
                return
            rows = valid_candles(payload["data"])
            if not rows:
                return
        except (ValueError, TypeError, KeyError, OverflowError):
            self.failed(socket)
            return
        self.last_message = time.monotonic()
        self.failures = 0
        self.receive(pair, rows)
        self.status("实时")

    def check(self):
        now = time.monotonic()
        if now - self.last_message >= 30:
            self.failed(self.socket)
        elif self.socket is not None and now - self.last_ping >= 15:
            self.last_ping = now
            self.socket.sendTextMessage("ping")

    def failed(self, socket):
        if socket is not self.socket or self.closed or self.retry.isActive():
            return
        self.drop()
        self.failures += 1
        self.status("轮询 · WebSocket 重连中")
        self.retry.start(min(30000, 1000 * 2 ** min(self.failures - 1, 5)))

    def drop(self):
        old, self.socket = self.socket, None
        self.watchdog.stop()
        if old is not None:
            old.abort()
            old.deleteLater()

    def close(self):
        self.closed = True
        self.retry.stop()
        self.drop()


class ChartFeed(QObject):
    def __init__(self, service):
        super().__init__(service)
        self.service = service
        self.generation = 0
        self.pending = set()
        self.history_state = {}
        self.revisions = {}
        self.stream_versions = {}
        self.stream_state = "等待行情"
        self.stream = None
        self.repairs = {}
        self.window_attempts = {}

    def activate(self):
        if self.service.closed:
            return
        if self.stream is None:
            self.stream = CandleStream(self.service.market.proxy, self.receive, self.status, self)
        self.stream.select((self.service.selected, self.service.bar))

    def status(self, value):
        self.stream_state = value
        if not self.service.closed:
            self.service.updated.emit("chart_status")

    def receive(self, pair, rows):
        now = time.monotonic()
        for row in rows:
            self.stream_versions[(pair, int(row[0]))] = now
        self.merge(pair, rows, fresh=True)

    def merge(self, pair, rows, *, fresh=False, started=None):
        s = self.service
        old = {int(row[0]): row for row in s.candles.get(pair, [])}
        for row in rows:
            stamp = int(row[0])
            previous = old.get(stamp)
            if previous and str(previous[8]) == "1" and str(row[8]) != "1":
                continue
            if started is not None and self.stream_versions.get((pair, stamp), 0) > started:
                if str(row[8]) != "1" or (previous and str(previous[8]) == "1"):
                    continue
            old[stamp] = row
        ordered = [old[t] for t in sorted(old)]
        if ordered != s.candles.get(pair, []):
            s.candles[pair] = ordered
            self.revisions[pair] = self.revisions.get(pair, 0) + 1
        if fresh and ordered and rows and int(rows[-1][0]) >= int(ordered[-1][0]) and pair not in self.repairs:
            latest = int(ordered[-1][0]) / 1000
            if -5 <= time.time() - latest <= BARS[pair[1]] + 30:
                s.candle_times[pair] = time.time()
            else:
                s.candle_times.pop(pair, None)
        s.updated.emit("candles")

    def fetch(self, inst, bar, *, older=False):
        s, pair = self.service, (inst, bar)
        key = (pair, older)
        if s.closed or key in self.pending:
            return
        current = s.candles.get(pair, [])
        if older and (not current or self.history_state.get(pair) == "已到历史边界"):
            return
        self.pending.add(key)
        generation, started = self.generation, time.monotonic()
        previous_last = int(current[-1][0]) if current else None
        params = {"instId": inst, "bar": bar, "limit": "300"}
        boundary = int(current[0][0]) if older else None
        if older:
            params["after"] = str(boundary)
            self.history_state[pair] = "加载更早 K 线…"
            s.updated.emit("chart_status")

        def done(rows, error):
            if generation != self.generation or s.closed:
                return
            self.pending.discard(key)
            try:
                if error:
                    raise ValueError(str(error))
                rows = valid_candles(rows or [])
            except (ValueError, TypeError, OverflowError):
                if older:
                    self.history_state[pair] = "历史加载失败 · 点击重试"
                elif time.time() - s.candle_times.get(pair, 0) > 30:
                    s.candle_times.pop(pair, None)
                s.updated.emit("chart_status")
                return
            if older:
                rows = [row for row in rows if int(row[0]) < boundary]
                self.history_state[pair] = "" if rows else "已到历史边界"
            elif rows and previous_last and int(rows[0][0]) > previous_last+BARS[bar]*1000:
                self.repairs[pair] = {"cursor": int(rows[0][0]), "target": previous_last}
                s.candle_times.pop(pair, None)
            self.merge(pair, rows, fresh=not older, started=started)
            if pair in self.repairs:
                QTimer.singleShot(350, lambda: self.repair(pair) if generation == self.generation else None)
        path = "/api/v5/market/history-candles" if older else "/api/v5/market/candles"
        s.api.get(path, done, params)

    def repair(self, pair):
        key = (pair, "repair")
        if self.service.closed or key in self.pending or pair not in self.repairs:
            return
        job = self.repairs[pair]
        cursor, generation = job["cursor"], self.generation
        self.pending.add(key)
        job.pop("failed", None)
        def done(rows, error):
            if generation != self.generation or self.service.closed:
                return
            self.pending.discard(key)
            try:
                if error:
                    raise ValueError(str(error))
                rows = [r for r in valid_candles(rows or []) if int(r[0]) < cursor]
                if not rows:
                    raise ValueError("未获取缺口数据")
            except (ValueError, TypeError, OverflowError):
                job["failed"] = True
                self.service.updated.emit("chart_status")
                return
            self.merge(pair, rows)
            job["cursor"] = int(rows[0][0])
            if job["cursor"] <= job["target"]+BARS[pair[1]]*1000:
                self.repairs.pop(pair, None)
                self.fetch(*pair)
            else:
                QTimer.singleShot(350, lambda: self.repair(pair) if generation == self.generation else None)
        self.service.api.get("/api/v5/market/history-candles", done,
            {"instId": pair[0], "bar": pair[1], "after": str(cursor), "limit": "300"})

    def ensure_range(self, pair, left, count):
        """恢复较早视口时直接请求该时间段，不从今天逐页下载数年的数据。"""
        rows = self.service.candles.get(pair, [])
        if not rows or left is None or self.service.closed:
            return
        interval = BARS[pair[1]]*1000
        offset = int(rows[0][0]) % interval
        begin = math.floor((left-offset)/interval)*interval+offset
        end = min(int(rows[-1][0]), left+count*interval)
        stamps = {int(r[0]) for r in rows}
        missing = next((t for t in range(int(begin), int(end)+1, interval) if t not in stamps), None)
        if missing is None:
            return
        after = min(missing+300*interval, end+interval)
        key = (pair, "window", int(after))
        if key in self.pending or time.monotonic()-self.window_attempts.get(key, -1e9) < 30:
            return
        self.pending.add(key)
        self.window_attempts[key] = time.monotonic()
        generation = self.generation
        def done(data, error):
            if generation != self.generation or self.service.closed:
                return
            self.pending.discard(key)
            try:
                if error:
                    raise ValueError(str(error))
                data = valid_candles(data or [])
            except (ValueError, TypeError, OverflowError):
                self.history_state[pair] = "历史加载失败 · 点击重试"
                self.service.updated.emit("chart_status")
                return
            self.history_state[pair] = "" if data else "该时间范围无可用 K 线"
            self.merge(pair, data)
        self.service.api.get("/api/v5/market/history-candles", done,
            {"instId": pair[0], "bar": pair[1], "after": str(int(after)), "limit": "300"})

    def text(self, pair):
        fresh = time.time() - self.service.candle_times.get(pair, 0) <= 30
        status = self.stream_state if fresh else "数据过期 / 等待同步"
        if pair in self.repairs:
            status += " · " + ("断线缺口待重试" if self.repairs[pair].get("failed") else "正在补齐断线缺口")
        return status + (" · " + self.history_state[pair] if self.history_state.get(pair) else "")

    def reset(self):
        self.generation += 1
        self.pending.clear()
        self.history_state.clear()
        self.stream_versions.clear()
        self.repairs.clear()
        self.window_attempts.clear()
        if self.stream:
            self.stream.close()
            self.stream.deleteLater()
            self.stream = None
        self.stream_state = "重连中"

    def close(self):
        self.reset()
