"""选中合约的 OKX 公共盘口、逐笔成交和衍生品快照。"""
import json
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebSockets import QWebSocket

from .microstructure import OrderBook, SequenceGap, TradeTape


class MarketMicro(QObject):
    changed = pyqtSignal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.instrument = None
        self.socket = None
        self.book = OrderBook()
        self.tape = TradeTape()
        self.derivatives = {}
        self.status = "未连接"
        self.generation = 0
        self.active = False
        self.last_message = self.last_ping = 0
        self.failures = 0
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.connect)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self.check)
        self.poll = QTimer(self)
        self.poll.setInterval(30000)
        self.poll.timeout.connect(self.fetch_derivatives)
        service.updated.connect(self.service_updated)

    def start(self):
        self.active = True
        self.select(self.service.selected)
        self.poll.start()

    def stop(self):
        self.active = False
        self.poll.stop()
        self.retry.stop()
        self.disconnect()

    def service_updated(self, kind):
        if kind == "selection" and self.active:
            self.select(self.service.selected)
        elif kind == "proxy" and self.active:
            self.disconnect()
            self.select(self.service.selected)

    def select(self, instrument):
        if instrument == self.instrument and self.socket is not None:
            return
        self.disconnect()
        self.instrument = instrument
        self.generation += 1
        self.book.reset()
        self.tape.reset()
        self.derivatives = {}
        self.status = "连接中"
        self.failures = 0
        self.changed.emit()
        if self.active:
            self.connect()
            self.fetch_derivatives()

    def connect(self):
        if not self.active or self.socket is not None or not self.instrument or self.service.closed:
            return
        socket = QWebSocket(parent=self)
        socket.setProxy(self.service.market.proxy)
        self.socket = socket
        self.last_message = self.last_ping = time.monotonic()
        self.watchdog.start()
        socket.connected.connect(lambda: self.connected(socket))
        socket.textMessageReceived.connect(lambda message: self.message(socket, message))
        socket.disconnected.connect(lambda: self.failed(socket))
        socket.errorOccurred.connect(lambda _: self.failed(socket))
        socket.open(QUrl("wss://ws.okx.com:8443/ws/v5/public"))

    def connected(self, socket):
        if socket is self.socket:
            socket.sendTextMessage(json.dumps({"op": "subscribe", "args": [
                {"channel": "books", "instId": self.instrument},
                {"channel": "trades", "instId": self.instrument}]}))
            self.status = "等待快照"
            self.changed.emit()

    def message(self, socket, message):
        if socket is not self.socket or message == "pong":
            return
        try:
            payload = json.loads(message)
            if payload.get("event") == "error":
                raise ValueError("订阅失败")
            arg = payload.get("arg", {})
            if arg.get("instId") != self.instrument:
                return
            channel = arg.get("channel")
            if channel == "books":
                for row in payload.get("data", []):
                    self.book.apply(payload.get("action", "update"), row)
                self.status = "实时" if self.book.sequence is not None else "等待快照"
            elif channel == "trades":
                self.tape.ingest(payload.get("data", []))
            else:
                return
        except (ValueError, TypeError, KeyError, SequenceGap):
            self.failed(socket)
            return
        self.last_message = time.monotonic()
        self.failures = 0
        self.changed.emit()

    def check(self):
        now = time.monotonic()
        if self.socket is None:
            return
        if now-self.last_message >= 30:
            self.failed(self.socket)
        elif now-self.last_ping >= 15:
            self.last_ping = now
            self.socket.sendTextMessage("ping")

    def failed(self, socket):
        if socket is not self.socket:
            return
        self.disconnect()
        self.book.reset()
        self.tape.reset()
        self.status = "数据中断 · 重建快照中"
        self.changed.emit()
        if self.active:
            self.failures += 1
            self.retry.start(min(30000, 1000*2**min(self.failures-1, 5)))

    def disconnect(self):
        old, self.socket = self.socket, None
        self.watchdog.stop()
        if old:
            old.abort()
            old.deleteLater()

    def fetch_derivatives(self):
        if not self.active or not self.instrument or self.service.closed:
            return
        generation, inst = self.generation, self.instrument
        paths = {
            "funding": ("/api/v5/public/funding-rate", {"instId": inst}),
            "oi": ("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}),
            "mark": ("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst}),
            "index": ("/api/v5/market/index-tickers", {"instId": inst.replace("-SWAP", "")})}
        for key, (path, params) in paths.items():
            self.service.api.get(path, lambda rows, error, k=key: self.received(k, rows, error, generation), params)

    def received(self, key, rows, error, generation):
        if generation != self.generation or not self.active or error or not rows:
            return
        row = rows[0]
        try:
            value = {"funding": row.get("fundingRate"), "oi": row.get("oiCcy") or row.get("oi"),
                     "mark": row.get("markPx"), "index": row.get("idxPx")}[key]
            if value is None:
                return
            stamp = int(row.get("ts") or time.time()*1000)/1000
            if not -5 <= time.time()-stamp <= 120:
                return
            self.derivatives[key] = {"value": str(value), "time": stamp, "source": "okx"}
            if "mark" in self.derivatives and "index" in self.derivatives:
                mark = float(self.derivatives["mark"]["value"])
                index = float(self.derivatives["index"]["value"])
                if index > 0:
                    self.derivatives["basis"] = {"value": str((mark/index-1)*100),
                        "time": min(self.derivatives["mark"]["time"], self.derivatives["index"]["time"]), "source": "okx"}
            self.changed.emit()
        except (ValueError, TypeError, OverflowError):
            return
