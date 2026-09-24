"""Qt WebSocket 行情：每个币种独立故障切换，推送合并后交给界面。"""

import json
from time import monotonic

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebSockets import QWebSocket

from .providers import SOURCE_ORDER, stream_subscription, parse_stream_price


class PriceStream(QObject):
    price_ready = pyqtSignal(str, object, str)
    unavailable = pyqtSignal(str)
    STALE_SECONDS = 30

    def __init__(self, symbol, source, proxy, parent=None, socket_factory=None, clock=monotonic):
        super().__init__(parent)
        self.symbol = symbol
        self.sources = SOURCE_ORDER if source == "auto" else (source,)
        self.index = 0
        self.proxy = proxy
        self.clock = clock
        self.socket_factory = socket_factory or (lambda: QWebSocket(parent=self))
        self.socket = None
        self.closed = False
        self.last_price_at = None
        self.last_price = None
        self.pending_price = None
        self.exchange_time = None
        self.failures = 0
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.start)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self._check)
        self.flush_timer = QTimer(self)
        self.flush_timer.setInterval(250)
        self.flush_timer.timeout.connect(self._flush)

    @property
    def source(self):
        return self.sources[self.index]

    def healthy(self):
        return (not self.closed and self.socket is not None and self.last_price_at is not None
                and self.clock() - self.last_price_at < self.STALE_SECONDS)

    def start(self):
        if self.closed or self.socket is not None:
            return
        self.retry.stop()
        self.started_at = self.last_ping_at = self.clock()
        self.watchdog.start()
        try:
            url, self.subscription = stream_subscription(self.source, self.symbol)
        except ValueError:
            self._fail()
            return
        socket = self.socket_factory()
        self.socket = socket
        socket.setProxy(self.proxy)
        socket.connected.connect(lambda: self._connected(socket))
        socket.textMessageReceived.connect(lambda message: self._message(socket, message))
        socket.disconnected.connect(lambda: self._socket_failed(socket))
        socket.errorOccurred.connect(lambda _: self._socket_failed(socket))
        socket.open(QUrl(url))

    def _connected(self, socket):
        if socket is self.socket and self.subscription is not None:
            socket.sendTextMessage(json.dumps(self.subscription))

    def _message(self, socket, message):
        if self.closed or socket is not self.socket:
            return
        try:
            price = parse_stream_price(self.source, self.symbol, message, self.last_price)
        except ValueError:
            self._fail()
            return
        if price is None:
            return
        if self.source == "okx":
            try:
                self.exchange_time = float(json.loads(message)["data"][0]["ts"]) / 1000
            except (KeyError, IndexError, TypeError, ValueError):
                self.exchange_time = None
        self.last_price_at = self.clock()
        self.last_price = price
        self.pending_price = price
        self.failures = 0
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def _flush(self):
        self.flush_timer.stop()
        price, self.pending_price = self.pending_price, None
        if price is not None and self.healthy():
            self.price_ready.emit(self.symbol, price, self.source)

    def _check(self):
        now = self.clock()
        reference = self.last_price_at if self.last_price_at is not None else self.started_at
        # 包含握手超时、订阅成功但无行情、以及仅收到心跳的假活跃连接。
        if now - reference >= self.STALE_SECONDS:
            self._fail()
        elif self.socket is not None and now - self.last_ping_at >= 15:
            self.last_ping_at = now
            if self.source == "okx":
                self.socket.sendTextMessage("ping")
            elif self.source == "bybit":
                self.socket.sendTextMessage('{"op":"ping"}')
            else:
                self.socket.ping()

    def _socket_failed(self, socket):
        if socket is self.socket:
            self._fail()

    def _drop_socket(self):
        socket, self.socket = self.socket, None
        self.watchdog.stop()
        self.flush_timer.stop()
        self.last_price_at = self.last_price = self.pending_price = None
        self.exchange_time = None
        if socket is not None:
            socket.abort()
            socket.deleteLater()

    def _fail(self):
        if self.closed or self.retry.isActive():
            return
        self._drop_socket()
        self.index = (self.index + 1) % len(self.sources)
        self.failures += 1
        self.retry.start(min(30_000, 1000 * 2 ** min(self.failures - 1, 5)))
        self.unavailable.emit(self.symbol)

    def close(self):
        self.closed = True
        self.retry.stop()
        self._drop_socket()
