"""Qt 异步 JSON 请求；POST 不自动重试，不跟随跨站重定向。"""
import json
import uuid
from dataclasses import dataclass

from PyQt6.QtCore import QByteArray, QObject, QTimer, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest


@dataclass
class ApiError:
    message: str
    code: str = ""
    uncertain: bool = False
    cancelled: bool = False

    def __str__(self):
        return self.message


class JsonTransport(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.pending = {}
        self.closed = False

    def set_proxy(self, config):
        self.cancel_all()
        if config.get("proxy_enabled"):
            kind = QNetworkProxy.ProxyType.Socks5Proxy if config["proxy_type"] == "socks5" else QNetworkProxy.ProxyType.HttpProxy
            proxy = QNetworkProxy(kind, config["proxy_host"], int(config["proxy_port"]))
        else:
            proxy = QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)
        self.manager.setProxy(proxy)
        self.manager.clearConnectionCache()

    def request(self, method, url, headers, body, callback, timeout=15000):
        request_id = uuid.uuid4().hex
        if self.closed:
            QTimer.singleShot(0, lambda: callback(None, ApiError("服务已关闭", cancelled=True)))
            return request_id
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(timeout)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        for key, value in headers.items():
            request.setRawHeader(key.encode(), value.encode())
        reply = self.manager.sendCustomRequest(request, method.encode(), QByteArray(body))
        self.pending[request_id] = (reply, callback, method)
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        timer.start(timeout + 1000)
        reply.finished.connect(lambda: self._finished(request_id))
        return request_id

    def _finished(self, request_id):
        entry = self.pending.pop(request_id, None)
        if entry is None:
            return
        reply, callback, method = entry
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
        raw = bytes(reply.readAll())
        network_error = reply.error() != QNetworkReply.NetworkError.NoError
        reply.deleteLater()
        if self.closed:
            return
        # 不把可能回显密钥的响应正文或 URL 放入错误消息。
        if network_error or not 200 <= status < 300:
            callback(None, ApiError(f"请求失败（HTTP {status or '无响应'}），请检查网络、地址与凭据",
                                    str(status), method != "GET" and (not status or status >= 500)))
            return
        try:
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError()
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            callback(None, ApiError("服务返回了无效 JSON", uncertain=method != "GET"))
            return
        callback(data, None)

    def cancel(self, request_id):
        entry = self.pending.pop(request_id, None)
        if entry:
            reply, callback, method = entry
            reply.abort()
            reply.deleteLater()
            if not self.closed:
                callback(None, ApiError("请求已取消", uncertain=method != "GET", cancelled=True))

    def cancel_all(self):
        for key in list(self.pending):
            self.cancel(key)

    def close(self):
        self.closed = True
        self.cancel_all()
