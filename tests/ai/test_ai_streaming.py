from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time
from collections.abc import Callable, Iterator

from PyQt6.QtWidgets import QApplication
import pytest

from coinpilot_ai.ai.models import JsonValue
from coinpilot_ai.ai.transport import AiStreamTransport, SseParser, StreamError, StreamFrame


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_sse_every_possible_chunk_boundary(newline: str) -> None:
    raw = ("\ufeff: heartbeat" + newline + "event: text" + newline + "data: 你好" + newline
           + "data: world" + newline + newline + "data:" + newline + newline).encode()
    expected = [StreamFrame("你好\nworld", "text"), StreamFrame("")]
    for boundary in range(len(raw) + 1):
        parser = SseParser()
        assert parser.feed(raw[:boundary]) + parser.feed(raw[boundary:], final=True) == expected
    parser = SseParser()
    frames: list[StreamFrame] = []
    for byte in raw:
        frames.extend(parser.feed(bytes([byte])))
    frames.extend(parser.feed(b"", final=True))
    assert frames == expected


def test_sse_limits_invalid_utf8_and_truncated_frame() -> None:
    with pytest.raises(ValueError):
        SseParser(max_frame_bytes=8).feed(b"data: 0123456789")
    with pytest.raises(UnicodeError):
        SseParser().feed(b"data: \xff\n\n")
    with pytest.raises(ValueError):
        SseParser().feed(b"data: partial\n", final=True)


def wait_until(app: QApplication, predicate: Callable[[], bool], *, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert predicate()


@contextmanager
def local_server() -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/json")
                self.end_headers()
                return
            if self.path == "/error":
                body = b"echoed-secret"
                self.send_response(500)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = b'data: {"text":"hello"}\n\ndata: [DONE]\n\n' if self.path in ("/sse", "/slow") else b'{"text":"hello"}'
            if self.path == "/malformed":
                body = b"echoed-secret"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if self.path in ("/sse", "/slow") else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.path == "/slow":
                time.sleep(0.2)
            try:
                for offset in range(0, len(body), 3):
                    self.wfile.write(body[offset:offset + 3])
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("endpoint", ["/sse", "/json", "/malformed", "/error", "/redirect"])
def test_qt_stream_and_json_fallback_and_sanitized_errors(app: QApplication, endpoint: str) -> None:
    with local_server() as (url, requests):
        transport = AiStreamTransport()
        frames: list[StreamFrame] = []
        results: list[tuple[dict[str, JsonValue] | None, StreamError | None]] = []
        transport.post(url + endpoint, {}, {}, frames.append, lambda data, error: results.append((data, error)))
        wait_until(app, lambda: bool(results))
        assert requests == [endpoint]  # POST 错误不重试，不跟随重定向。
        if endpoint == "/sse":
            assert frames == [StreamFrame('{"text":"hello"}'), StreamFrame("[DONE]")]
            assert results == [(None, None)]
        elif endpoint == "/json":
            assert results == [({"text": "hello"}, None)]
        else:
            assert results[0][1] is not None
            assert "echoed-secret" not in results[0][1].message
        transport.close()


@pytest.mark.parametrize("action", ["cancel", "proxy", "timeout", "close", "size"])
def test_cancel_timeout_proxy_and_response_limit(app: QApplication, action: str) -> None:
    with local_server() as (url, requests):
        transport = AiStreamTransport()
        if action == "size":
            transport.MAX_RESPONSE_BYTES = 8
        results: list[StreamError | None] = []
        handle = transport.post(url + "/slow", {}, {}, lambda frame: None,
                                lambda data, error: results.append(error), timeout=20 if action == "timeout" else 1000)
        if action == "cancel":
            handle.cancel()
        elif action == "proxy":
            transport.set_proxy({"proxy_enabled": False})
        elif action == "close":
            transport.close()
        wait_until(app, lambda: bool(results))
        assert len(results) == 1 and results[0] is not None
        if action in ("cancel", "proxy", "close"):
            assert results[0].cancelled
        transport.close()
        app.processEvents()
        assert len(results) == 1
