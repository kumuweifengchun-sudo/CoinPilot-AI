import json
from decimal import Decimal

import pytest
from PyQt6.QtNetwork import QNetworkProxy

from coinpilot_ai.market.client import MarketClient
from coinpilot_ai.market.providers import parse_stream_price, stream_subscription
from coinpilot_ai.market.streaming import PriceStream
from test_network import Manager


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class Socket:
    def __init__(self):
        self.connected = Signal()
        self.textMessageReceived = Signal()
        self.disconnected = Signal()
        self.errorOccurred = Signal()
        self.sent = []
        self.aborted = self.deleted = False

    def setProxy(self, proxy):
        self.proxy = proxy

    def open(self, url):
        self.url = url.toString()

    def sendTextMessage(self, text):
        self.sent.append(text)

    def ping(self):
        self.sent.append("<ping frame>")

    def abort(self):
        self.aborted = True
        self.disconnected.emit()

    def deleteLater(self):
        self.deleted = True


def ticker(price="123", symbol="BTCUSDT"):
    return json.dumps({"e": "24hrTicker", "s": symbol, "c": price})


@pytest.fixture
def stream(app):
    now = [100.0]
    instance = PriceStream("BTCUSDT", "auto", QNetworkProxy(), socket_factory=Socket,
                           clock=lambda: now[0])
    instance.test_time = now
    instance.start()
    yield instance
    instance.close()


@pytest.mark.parametrize("source,message", [
    ("binance", ticker()),
    ("okx", json.dumps({"arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
                         "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "last": "123"}]})),
    ("bybit", json.dumps({"topic": "tickers.BTCUSDT", "type": "snapshot",
                           "data": {"symbol": "BTCUSDT", "lastPrice": "123"}})),
])
def test_exchange_prices(source, message):
    assert parse_stream_price(source, "BTCUSDT", message) == Decimal("123")


def test_delta_without_price_and_subscription_messages():
    message = json.dumps({"topic": "tickers.BTCUSDT", "type": "delta",
                          "data": {"symbol": "BTCUSDT", "bid1Price": "99"}})
    assert parse_stream_price("bybit", "BTCUSDT", message, Decimal("123")) == Decimal("123")
    assert parse_stream_price("bybit", "BTCUSDT", message) is None
    for source, message in [("okx", "pong"), ("bybit", '{"success":true,"op":"subscribe"}'),
                            ("binance", ticker(symbol="ETHUSDT"))]:
        assert parse_stream_price(source, "BTCUSDT", message) is None


def test_binance_ticker_uses_market_endpoint():
    assert stream_subscription("binance", "BTCUSDT") == (
        "wss://fstream.binance.com/market/ws/btcusdt@ticker", None)


@pytest.mark.parametrize("message", ["bad", "[]", '{"code":-1}', ticker("NaN"), ticker("0"),
                                     ticker("Infinity"), '{"event":"error"}', '{"success":false}'])
def test_invalid_stream_data(message):
    with pytest.raises(ValueError):
        parse_stream_price("binance", "BTCUSDT", message)


def test_burst_coalesces_and_latest_price_wins(stream):
    results = []
    stream.price_ready.connect(lambda *args: results.append(args))
    for price in range(1, 100):
        stream.socket.textMessageReceived.emit(ticker(str(price)))
    assert stream.healthy() and not results
    stream._flush()
    assert results == [("BTCUSDT", Decimal("99"), "binance")]
    stream._flush()
    assert len(results) == 1


def test_disconnect_rotates_once_and_ignores_late_messages(stream):
    old = stream.socket
    old.errorOccurred.emit(1)
    old.disconnected.emit()
    old.textMessageReceived.emit(ticker())
    assert stream.source == "okx" and stream.retry.interval() == 1000
    assert not stream.healthy() and stream.pending_price is None
    stream.start()
    stream.socket.connected.emit()
    assert json.loads(stream.socket.sent[0])["args"][0]["instId"] == "BTC-USDT-SWAP"
    assert old.aborted and old.deleted


def test_heartbeat_does_not_hide_stale_subscription(stream):
    stream.test_time[0] += 15
    stream._check()
    assert stream.socket.sent == ["<ping frame>"]
    stream.socket.textMessageReceived.emit("pong")
    stream.test_time[0] += 15
    stream._check()
    assert stream.socket is None and stream.source == "okx"


def test_fixed_source_backoff_and_shutdown(app):
    stream = PriceStream("BTCUSDT", "bybit", QNetworkProxy(), socket_factory=Socket)
    for attempt in range(8):
        stream.start()
        stream.socket.errorOccurred.emit(1)
        assert stream.source == "bybit"
        assert stream.retry.interval() == min(30_000, 1000 * 2 ** attempt)
    stream.close()
    stream.start()
    assert not stream.retry.isActive() and stream.socket is None


@pytest.fixture
def client(app, tmp_path):
    def factory(*args, **kwargs):
        return PriceStream(*args, socket_factory=Socket, **kwargs)
    instance = MarketClient(tmp_path, manager=Manager(), stream_factory=factory)
    yield instance
    instance.close()


def test_stream_suppresses_polling_and_stale_http_results(client):
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT", "BTCUSDT"])
    stream = client.streams["BTCUSDT"]
    old_reply = client.manager.replies[0]
    stream.socket.textMessageReceived.emit(ticker("200"))
    old_reply.complete(status=503)  # 推送已到达但尚未刷新界面，HTTP 错误也不能覆盖。
    assert not results and not client.price_jobs
    stream._flush()
    client.refresh_prices(["BTCUSDT"])
    assert len(client.manager.requests) == 1
    assert results == [("BTCUSDT", Decimal("200"), "", "binance")]
    stream.socket.errorOccurred.emit(1)
    assert len(client.manager.requests) == 2
    client.manager.replies[-1].complete()
    assert len(results) == 2


def test_stream_aborts_pending_http_and_symbol_removal(client):
    client.refresh_prices(["BTCUSDT", "ETHUSDT"])
    stream = client.streams["BTCUSDT"]
    stream.socket.textMessageReceived.emit(ticker())
    stream._flush()
    assert client.manager.replies[0].aborted
    client.refresh_prices(["ETHUSDT"])
    assert stream.closed and set(client.streams) == {"ETHUSDT"}


def test_proxy_and_source_changes_clear_old_streams(client):
    client.refresh_prices(["BTCUSDT"])
    old = client.streams["BTCUSDT"]
    client.set_proxy({"proxy_enabled": False})
    assert old.closed and not client.streams
    client.refresh_prices(["BTCUSDT"])
    assert client.streams["BTCUSDT"].socket.proxy.type() == QNetworkProxy.ProxyType.NoProxy
    old = client.streams["BTCUSDT"]
    client.set_source("okx")
    assert old.closed and not client.streams
    client.refresh_prices(["BTCUSDT"])
    assert client.streams["BTCUSDT"].source == "okx"
    client.close()
    assert not client.streams and not client.pending


@pytest.mark.parametrize("source", ["okx", "bybit"])
def test_exchange_specific_heartbeat(app, source):
    now = [100.0]
    stream = PriceStream("BTCUSDT", source, QNetworkProxy(), socket_factory=Socket, clock=lambda: now[0])
    stream.start()
    stream.socket.connected.emit()
    now[0] += 15
    stream._check()
    expected = "ping" if source == "okx" else '{"op":"ping"}'
    assert stream.socket.sent[-1] == expected
    assert stream_subscription(source, "BTCUSDT")[0].startswith("wss://")
    stream.close()
