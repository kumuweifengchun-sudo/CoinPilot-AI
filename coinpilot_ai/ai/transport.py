"""专供 AI 的异步流式传输；不改变交易使用的 JsonTransport。"""

from collections.abc import Callable
import codecs
from dataclasses import dataclass, field
import json
import re
from typing import Literal
import uuid

from PyQt6.QtCore import QByteArray, QObject, QTimer, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest

from coinpilot_ai.ui.qt import require
from .models import JsonValue, validate_payload


@dataclass(frozen=True)
class StreamFrame:
    data: str
    event: str = "message"


@dataclass(frozen=True)
class StreamError:
    message: str
    cancelled: bool = False


@dataclass(frozen=True)
class RequestHandle:
    id: str
    _cancel: Callable[[], None] = field(repr=False)

    def cancel(self) -> None:
        self._cancel()


class SseParser:
    """UTF-8、CR/LF/CRLF 及字段均可跨任意网络块。忽略注释和非 data/event 字段。"""

    def __init__(self, *, max_frame_bytes: int = 1024 * 1024) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self._buffer = ""
        self._data: list[str] = []
        self._event = "message"
        self._size = 0
        self._limit = max_frame_bytes

    def feed(self, chunk: bytes, *, final: bool = False) -> list[StreamFrame]:
        self._buffer += self._decoder.decode(chunk, final=final)
        frames: list[StreamFrame] = []
        while match := re.search(r"\r\n|\r|\n", self._buffer):
            if match.group() == "\r" and match.end() == len(self._buffer) and not final:
                break
            line, self._buffer = self._buffer[:match.start()], self._buffer[match.end():]
            self._size += len(line.encode("utf-8")) + len(match.group())
            if self._size > self._limit:
                raise ValueError("SSE 数据帧超过大小限制")
            if not line:
                if self._data:
                    frames.append(StreamFrame("\n".join(self._data), self._event))
                self._data.clear()
                self._event, self._size = "message", 0
                continue
            name, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if name == "data":
                self._data.append(value)
            elif name == "event":
                self._event = value or "message"
        if self._size + len(self._buffer.encode("utf-8")) > self._limit:
            raise ValueError("SSE 数据帧超过大小限制")
        if final and (self._buffer or self._data):
            raise ValueError("SSE 数据帧未完整结束")
        return frames


type FrameCallback = Callable[[StreamFrame], None]
type StreamCallback = Callable[[dict[str, JsonValue] | None, StreamError | None], None]


@dataclass
class _Pending:
    reply: QNetworkReply
    timer: QTimer
    on_frame: FrameCallback
    on_done: StreamCallback
    parser: SseParser = field(default_factory=SseParser)
    mode: Literal["unknown", "sse", "json"] = "unknown"
    raw: bytearray = field(default_factory=bytearray)
    received: int = 0


class AiStreamTransport(QObject):
    MAX_RESPONSE_BYTES: int = 16 * 1024 * 1024

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._pending: dict[str, _Pending] = {}
        self.closed = False

    def set_proxy(self, config: dict[str, object]) -> None:
        self.cancel_all()
        if config.get("proxy_enabled"):
            host, port = config.get("proxy_host"), config.get("proxy_port")
            if not isinstance(host, str) or not isinstance(port, (int, str)) or isinstance(port, bool):
                raise ValueError("无效的代理主机或端口")
            port_number = int(port)
            if not 1 <= port_number <= 65535 or config.get("proxy_type") not in ("socks5", "http"):
                raise ValueError("无效的代理类型或端口")
            kind = (QNetworkProxy.ProxyType.Socks5Proxy if config["proxy_type"] == "socks5"
                    else QNetworkProxy.ProxyType.HttpProxy)
            proxy = QNetworkProxy(kind, host, port_number)
        else:
            proxy = QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)
        self.manager.setProxy(proxy)
        self.manager.clearConnectionCache()

    def post(
        self, url: str, headers: dict[str, str], body: dict[str, JsonValue],
        on_frame: FrameCallback, on_done: StreamCallback, *, timeout: int = 90000,
    ) -> RequestHandle:
        request_id = uuid.uuid4().hex
        handle = RequestHandle(request_id, lambda: self.cancel(request_id))
        if self.closed:
            QTimer.singleShot(0, lambda: on_done(None, StreamError("AI 服务已关闭", cancelled=True)))
            return handle
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(timeout)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Accept", b"text/event-stream, application/json")
        for name, value in headers.items():
            request.setRawHeader(name.encode(), value.encode())
        encoded = json.dumps(validate_payload(body), ensure_ascii=False, allow_nan=False).encode()
        reply = require(self.manager.post(request, QByteArray(encoded)))
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._stop(request_id, StreamError("AI 请求超时")))
        self._pending[request_id] = _Pending(reply, timer, on_frame, on_done)
        reply.readyRead.connect(lambda: self._read(request_id))
        reply.finished.connect(lambda: self._finished(request_id))
        timer.start(timeout)
        return handle

    def _read(self, request_id: str, *, final: bool = False) -> None:
        entry = self._pending.get(request_id)
        if entry is None:
            return
        status: object = entry.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        # 错误响应可能回显凭据，不交给协议解析器，也不放进异常。
        if not isinstance(status, int) or not 200 <= status < 300:
            return
        chunk = entry.reply.readAll().data()
        entry.received += len(chunk)
        if entry.received > self.MAX_RESPONSE_BYTES:
            self._stop(request_id, StreamError("AI 响应超过大小限制"))
            return
        if entry.mode == "unknown":
            content_type: object = entry.reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader)
            entry.mode = "sse" if isinstance(content_type, str) and "text/event-stream" in content_type.lower() else "json"
        try:
            if entry.mode == "sse":
                for frame in entry.parser.feed(chunk, final=final):
                    if request_id not in self._pending:
                        break
                    entry.on_frame(frame)
            else:
                entry.raw.extend(chunk)
        except (ValueError, UnicodeError, RecursionError):
            self._stop(request_id, StreamError("AI 返回了无效的流式数据"))

    def _finished(self, request_id: str) -> None:
        entry = self._pending.get(request_id)
        if entry is None:
            return
        status: object = entry.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if entry.reply.error() != QNetworkReply.NetworkError.NoError or not isinstance(status, int) or not 200 <= status < 300:
            self._stop(request_id, StreamError("AI 请求失败，请检查网络、地址与凭据"))
            return
        self._read(request_id, final=True)
        entry = self._pending.pop(request_id, None)
        if entry is None:
            return
        entry.timer.stop()
        entry.reply.deleteLater()
        if entry.mode == "sse":
            entry.on_done(None, None)
        else:
            try:
                raw: object = json.loads(entry.raw)
                result = validate_payload(raw)
            except (ValueError, UnicodeError, RecursionError):
                entry.on_done(None, StreamError("AI 返回了无效 JSON"))
                return
            entry.on_done(result, None)

    def _stop(self, request_id: str, error: StreamError) -> None:
        entry = self._pending.pop(request_id, None)
        if entry is not None:
            entry.timer.stop()
            entry.reply.abort()
            entry.reply.deleteLater()
            entry.on_done(None, error)

    def cancel(self, request_id: str) -> None:
        self._stop(request_id, StreamError("AI 请求已取消", cancelled=True))

    def cancel_all(self) -> None:
        for request_id in list(self._pending):
            self.cancel(request_id)

    def close(self) -> None:
        self.closed = True
        self.cancel_all()
