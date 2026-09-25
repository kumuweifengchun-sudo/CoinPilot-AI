"""本地 HTTP 合约测试：实际 Qt 网络请求，无外部账户和公网依赖。"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PyQt6.QtTest import QTest

from coinpilot_ai.review.ai import AiClient
from coinpilot_ai.trading.history import HistorySync
from coinpilot_ai.core.credentials import CredentialVault
from coinpilot_ai.core.store import Store
from coinpilot_ai.integrations.transport import JsonTransport


@pytest.fixture
def http_server():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            seen.append((self.path, dict(self.headers), body))
            if self.path.endswith("slow"):
                time.sleep(.4)
                value = {"ok": True}
            elif self.path.endswith("responses"):
                value = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "responses ok"}]}]}
            elif self.path.endswith("messages"):
                value = {"content": [{"type": "text", "text": "messages ok"}]}
            else:
                value = {"choices": [{"message": {"content": "chat ok"}}]}
            self.send_response(500 if self.path.endswith("error") else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(value).encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            seen.append((self.path, dict(self.headers), None))
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/do-not-follow")
            self.end_headers()

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def wait_for(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert predicate(), "异步操作未完成"


@pytest.mark.parametrize("protocol", ["responses", "messages", "chat"])
def test_three_protocols_over_real_qt_http(app, http_server, protocol):
    base, seen = http_server
    transport = JsonTransport()
    transport.set_proxy({"proxy_enabled": False})
    class Vault:
        def read(self, key):
            return {"key": "dummy-test-key"}
    client = AiClient(transport, Vault())
    config = {"id": "test", "protocol": protocol, "model": "test", "base_url": base + "/v1"}
    results = []
    client.ask(config, "测试内容", lambda text, error: results.append((text, error)))
    wait_for(app, lambda: bool(results))
    assert results == [(protocol + " ok", None)]
    assert len(seen) == 1 and "测试内容" in json.dumps(seen[0][2], ensure_ascii=False)
    transport.close()


def test_cancel_error_and_no_redirect_preserve_uncertainty(app, http_server):
    base, seen = http_server
    transport = JsonTransport()
    transport.set_proxy({"proxy_enabled": False})
    result = []
    key = transport.request("POST", base + "/slow", {}, b"{}", lambda value, error: result.append(error))
    wait_for(app, lambda: bool(seen))
    transport.cancel(key)
    assert len(result) == 1 and result[0].cancelled and result[0].uncertain
    QTest.qWait(450)
    assert len(result) == 1
    result.clear()
    transport.request("POST", base + "/error", {}, b"{}", lambda value, error: result.append(error))
    wait_for(app, lambda: bool(result))
    assert result[0].uncertain
    result.clear()
    transport.request("GET", base + "/redirect", {"Authorization": "secret"}, b"", lambda value, error: result.append(error))
    wait_for(app, lambda: bool(result))
    assert result[0].code == "302"
    assert len(seen) == 3
    transport.close()


def test_history_pagination_dedup_and_failure_coverage(app, tmp_path):
    store = Store(tmp_path / "history.db")
    class Api:
        def __init__(self):
            self.calls = []
        def get(self, path, callback, params=None, private=False):
            self.calls.append((path, dict(params)))
            if "fills-history" in path:
                if "after" not in params:
                    rows = [{"instId": "BTC-USDT-SWAP", "billId": str(1000-i), "ts": "2000000"} for i in range(100)]
                else:
                    rows = [{"instId": "BTC-USDT-SWAP", "billId": "900", "ts": "1900000"}]
                callback(rows, None)
            elif "bills-archive" in path:
                from coinpilot_ai.integrations.transport import ApiError
                callback(None, ApiError("forbidden", "403"))
            else:
                callback([], None)
    api = Api()
    done = []
    sync = HistorySync(api, store, "demo:x", lambda *_: None, done.append)
    assert sync.start(1000, 3000)
    assert not sync.start(1000, 3000)
    wait_for(app, lambda: bool(done), timeout=4)
    assert len(store.list("fills", "demo:x")) == 101
    assert api.calls[1][1]["after"] == "901"
    assert not done[0]["complete"]
    assert "bills" in done[0]["results"]
    assert store.get("state", "history_cursor", scope="demo:x") is None
    store.close()


def test_windows_credential_roundtrip_isolated_namespace():
    import sys
    import uuid
    if sys.platform != "win32":
        pytest.skip("仅 Windows 凭据管理器")
    vault = CredentialVault("CoinPilotAI-Test/" + uuid.uuid4().hex)
    try:
        assert vault.read("test") is None
        vault.write("test", {"key": "non-sensitive-test-value", "secret": "中文"})
        assert vault.read("test") == {"key": "non-sensitive-test-value", "secret": "中文"}
    finally:
        vault.delete("test")


def test_history_future_ranges_and_older_sync_do_not_advance_wrong_cursor(app, tmp_path):
    store = Store(tmp_path / "range.db")
    class Api:
        def get(self, path, callback, params=None, private=False):
            callback([], None)
    done = []
    sync = HistorySync(Api(), store, "demo", lambda *_: None, done.append)
    with pytest.raises(ValueError):
        sync.start(time.time()+3600, time.time()+7200)
    store.put("state", "history_cursor", 10000, "demo")
    sync.start(1000, 3000)
    wait_for(app, lambda: bool(done), timeout=4)
    assert store.get("state", "history_cursor", scope="demo") == 10000
    store.close()
