from decimal import Decimal

import pytest
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest

from coinpilot_ai.market.client import MarketClient, parse_price


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self):
        for callback in self.callbacks:
            callback()


class Reply:
    def __init__(self):
        self.finished = Signal()
        self.aborted = False
        self.deleted = False
        self.data = b""
        self.status = 200
        self.network_error = QNetworkReply.NetworkError.NoError

    def error(self):
        return self.network_error

    def attribute(self, _):
        return self.status

    def readAll(self):
        return self.data

    def abort(self):
        self.aborted = True

    def deleteLater(self):
        self.deleted = True

    def complete(self, data=b'{"symbol":"BTCUSDT","price":"123.456"}', status=200, error=None):
        self.data, self.status = data, status
        self.network_error = error or QNetworkReply.NetworkError.NoError
        self.finished.emit()


class Manager:
    def __init__(self):
        self.requests = []
        self.replies = []

    def setProxy(self, proxy):
        self.proxy = proxy

    def clearConnectionCache(self):
        pass

    def get(self, request):
        self.requests.append(request)
        reply = Reply()
        self.replies.append(reply)
        return reply


@pytest.fixture
def client(app, tmp_path):
    instance = MarketClient(tmp_path, manager=Manager(), source="binance", streaming=False)
    yield instance
    instance.close()


def test_pending_requests_deduplicate_and_release(client):
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT", "BTCUSDT"])
    client.refresh_prices(["BTCUSDT"])
    assert len(client.manager.requests) == 1
    assert client.manager.requests[0].transferTimeout() == 3000
    client.manager.replies[0].complete()
    assert results == [("BTCUSDT", Decimal("123.456"), "", "binance")]
    assert not client.pending
    client.refresh_prices(["BTCUSDT"])
    assert len(client.manager.requests) == 2


@pytest.mark.parametrize("data", [b"{}", b"[]", b"null", b"bad", b'{"price":"NaN"}',
                                      b'{"price":"-3"}', b'{"price":0}', b'{"price":"1e1000"}'])
def test_invalid_response(data):
    with pytest.raises(ValueError):
        parse_price(data)


@pytest.mark.parametrize("status,error", [(400, None), (500, None),
    (None, QNetworkReply.NetworkError.TimeoutError),
    (None, QNetworkReply.NetworkError.HostNotFoundError)])
def test_failure_emits_chinese_status(client, status, error):
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT"])
    client.manager.replies[-1].complete(status=status, error=error)
    assert results[-1][1] is None
    assert results[-1][2] in ("Binance：交易对无效", "Binance：网络请求失败")
    assert not client.pending


def test_old_reply_cannot_replace_new_request(client):
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT"])
    old = client.manager.replies[-1]
    client.cancel("price")
    client.refresh_prices(["BTCUSDT"])
    new = client.manager.replies[-1]
    old.complete(b'{"price":"1"}')
    assert results == []
    assert client.pending[("price", "BTCUSDT")] is new
    new.complete(b'{"symbol":"BTCUSDT","price":"2"}')
    assert results[0][1] == Decimal("2")
    assert old.aborted and old.deleted


def png_bytes():
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff9900"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_icons_validate_cache_and_report_fallback(client, tmp_path):
    icons, finished = [], []
    client.icon_ready.connect(lambda *args: icons.append(args))
    client.icons_finished.connect(lambda *args: finished.append(args))
    (tmp_path / "BTC.png").write_bytes(b"broken")
    client.reload_icons(["BTCUSDT", "SOLUSDT"])
    client.manager.replies[0].complete(png_bytes())
    client.manager.replies[1].complete(b"not an image")
    assert len(icons) == 1
    assert not QImage(str(tmp_path / "BTC.png")).isNull()
    assert not (tmp_path / "SOL.png").exists()
    assert finished == [(1, 1)]
    count = len(client.manager.requests)
    client.reload_icons(["BTCUSDT"])
    assert len(client.manager.requests) == count


def test_icon_reload_invalidates_inflight_download(client, tmp_path):
    client.reload_icons(["BTCUSDT"])
    old = client.manager.replies[-1]
    client.reload_icons(["BTCUSDT"], clear=True)
    old.complete(png_bytes())
    assert not (tmp_path / "BTC.png").exists()
    assert old.aborted


def test_shutdown_aborts_all_and_ignores_late_results(client):
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT", "ETHUSDT"])
    client.reload_icons(["BTCUSDT"])
    client.close()
    assert not client.pending
    for reply in client.manager.replies:
        assert reply.aborted and reply.deleted
        reply.complete()
    assert results == []
    count = len(client.manager.requests)
    client.refresh_prices(["SOLUSDT"])
    assert len(client.manager.requests) == count


def test_auto_fallback_deduplicates_full_chain_and_reports_actual_source(client):
    client.set_source("auto")
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT"])
    client.manager.replies[0].complete(error=QNetworkReply.NetworkError.TimeoutError)
    assert "www.okx.com" in client.manager.requests[1].url().toString()
    client.refresh_prices(["BTCUSDT"])
    assert len(client.manager.requests) == 2
    assert results == []
    client.manager.replies[1].complete(b'{"code":"51001","data":[]}')
    assert "category=linear" in client.manager.requests[2].url().toString()
    client.manager.replies[2].complete(b'{"retCode":0,"result":{"category":"linear","list":[{"symbol":"BTCUSDT","lastPrice":"99"}]}}')
    assert results == [("BTCUSDT", Decimal("99"), "", "bybit")]
    assert not client.pending and not client.price_jobs
    client.refresh_prices(["BTCUSDT"])
    assert "api.binance.com" in client.manager.requests[-1].url().toString()


def test_all_sources_fail_once_then_next_cycle_can_retry(client):
    client.set_source("auto")
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT"])
    for i in range(3):
        client.manager.replies[i].complete(status=503)
    assert len(results) == 1
    assert results[0][1] is None and results[0][3] == ""
    assert all(name in results[0][2] for name in ("Binance", "OKX", "Bybit"))
    assert not client.price_jobs
    client.refresh_prices(["BTCUSDT"])
    assert len(client.manager.requests) == 4


def test_manual_source_change_cancels_fallback_and_ignores_late_reply(client):
    client.set_source("auto")
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT"])
    client.manager.replies[0].complete(status=503)
    old = client.manager.replies[1]
    client.set_source("bybit")
    assert old.aborted and not client.price_jobs
    client.refresh_prices(["BTCUSDT"])
    old.complete(b'{"code":"0","data":[{"instId":"BTC-USDT-SWAP","instType":"SWAP","last":"88"}]}')
    assert results == []
    client.manager.replies[2].complete(status=500)
    assert len(client.manager.requests) == 3  # 固定模式不会访问其他源。
    assert len(results) == 1 and "Bybit" in results[0][2]


def test_auto_jobs_are_independent_per_symbol(client):
    client.set_source("auto")
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT", "ETHUSDT"])
    client.manager.replies[0].complete(status=429)
    client.manager.replies[1].complete(b'{"symbol":"ETHUSDT","price":"2000"}')
    client.manager.replies[2].complete(b'{"code":"0","data":[{"instId":"BTC-USDT-SWAP","instType":"SWAP","last":"88"}]}')
    assert [(row[0], row[3]) for row in results] == [("ETHUSDT", "binance"), ("BTCUSDT", "okx")]


def test_proxy_change_cancels_both_request_types_and_ignores_old_results(client):
    from PyQt6.QtNetwork import QNetworkProxy
    assert client.manager.proxy.type() == QNetworkProxy.ProxyType.Socks5Proxy
    assert client.manager.proxy.hostName() == "127.0.0.1"
    assert client.manager.proxy.port() == 7897
    results = []
    client.price_ready.connect(lambda *args: results.append(args))
    client.refresh_prices(["BTCUSDT"])
    client.reload_icons(["BTCUSDT"])
    old = client.manager.replies.copy()
    client.set_proxy({"proxy_type": "http", "proxy_host": "localhost", "proxy_port": 8080})
    assert all(reply.aborted for reply in old)
    assert not client.pending and not client.price_jobs
    assert client.manager.proxy.type() == QNetworkProxy.ProxyType.HttpProxy
    assert client.manager.proxy.port() == 8080
    client.refresh_prices(["BTCUSDT"])
    for reply in old:
        reply.complete()
    assert not results
    client.manager.replies[-1].complete()
    assert len(results) == 1
    client.set_proxy({"proxy_enabled": False})
    assert client.manager.proxy.type() == QNetworkProxy.ProxyType.NoProxy


def test_real_qt_manager_applies_proxy(app, tmp_path):
    from PyQt6.QtNetwork import QNetworkProxy
    client = MarketClient(tmp_path)
    assert client.manager.proxy().type() == QNetworkProxy.ProxyType.Socks5Proxy
    client.set_proxy({"proxy_enabled": False})
    assert client.manager.proxy().type() == QNetworkProxy.ProxyType.NoProxy
    client.close()
