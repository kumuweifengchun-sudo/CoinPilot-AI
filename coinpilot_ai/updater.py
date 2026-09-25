"""公开正式版检查与流式下载；只信任固定发行源，不在下载完成时自动执行。"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest

from .version import VERSION

REPOSITORY = "kumuweifengchun-sudo/CoinPilot-AI"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_INSTALLER = 512 * 1024 * 1024


def version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value):
        raise ValueError("发行版版本号无效")
    parts = tuple(map(int, value.split(".")))
    if any(part > 65535 for part in parts):
        raise ValueError("发行版版本号超出支持范围")
    return parts + (0,) * (4-len(parts))


@dataclass(frozen=True)
class Release:
    version: str
    filename: str
    url: str
    checksum_url: str
    size: int
    notes: str


def parse_release(payload, current=VERSION):
    if not isinstance(payload, dict):
        raise ValueError("发行源返回了无效数据")
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name", "")
    if not isinstance(tag, str):
        raise ValueError("发行版标签无效")
    version = tag.removeprefix("v")
    if version_key(version) <= version_key(current):
        return None
    filename = f"CoinPilotAI-Setup-{version}-x64.exe"
    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("发行版附件无效")
    selected = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") in (filename, filename+".sha256"):
            name = asset["name"]
            expected = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
            if name in selected or asset.get("browser_download_url") != expected:
                raise ValueError("发行版附件地址或名称不匹配")
            selected[name] = asset
    if filename not in selected or filename+".sha256" not in selected:
        raise ValueError("新版缺少 Windows 安装包或 SHA-256 校验文件，请等待发布完成")
    size = selected[filename].get("size")
    if type(size) is not int or not 0 < size <= MAX_INSTALLER:
        raise ValueError("安装包大小无效或超过 512 MB")
    notes = payload.get("body") or "此版本未提供更新说明。"
    return Release(version, filename, selected[filename]["browser_download_url"],
                   selected[filename+".sha256"]["browser_download_url"], size, str(notes)[:20000])


def parse_checksum(raw, filename):
    try:
        text = raw.decode("utf-8-sig").strip()
    except UnicodeError:
        raise ValueError("校验文件编码无效") from None
    match = re.fullmatch(r"([a-fA-F0-9]{64})[ \t]+\*?"+re.escape(filename), text)
    if match is None:
        raise ValueError("SHA-256 校验文件与安装包不匹配")
    return match[1].lower()


def allowed_request(url, kind):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.fragment or parts.port not in (None, 443):
        return False
    if kind == "metadata":
        return url == RELEASE_API
    if parts.hostname == "github.com":
        return parts.path.startswith(f"/{REPOSITORY}/releases/download/")
    return parts.hostname in ("release-assets.githubusercontent.com", "objects.githubusercontent.com")


class UpdateClient(QObject):
    changed = pyqtSignal(str)
    available = pyqtSignal(object)
    progress = pyqtSignal(int, int)

    def __init__(self, cache_dir, config, parent=None, *, manager=None, current=VERSION):
        super().__init__(parent)
        self.manager = manager if manager is not None else QNetworkAccessManager(self)
        self.cache_dir, self.current = Path(cache_dir), current
        self.state, self.message = "idle", "可以检查是否有新版本。"
        self.release = self.downloaded = None
        self.checksum = ""
        self.reply = self.file = self.part = None
        self.serial, self.received = 0, 0
        self.closed = False
        self.proxy_config = None
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self._fail("更新请求超时，请检查网络后重试。"))
        self.set_proxy(config)

    def _state(self, state, message):
        self.state, self.message = state, message
        self.changed.emit(state)

    def set_proxy(self, config):
        settings = tuple(config.get(key) for key in ("proxy_enabled", "proxy_type", "proxy_host", "proxy_port"))
        if settings == self.proxy_config:
            return
        if self.reply is not None:
            self.cancel()
        if config.get("proxy_enabled"):
            kind = QNetworkProxy.ProxyType.Socks5Proxy if config["proxy_type"] == "socks5" else QNetworkProxy.ProxyType.HttpProxy
            proxy = QNetworkProxy(kind, config["proxy_host"], config["proxy_port"])
        else:
            proxy = QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)
        self.manager.setProxy(proxy)
        self.manager.clearConnectionCache()
        self.proxy_config = settings

    def check(self):
        if self.closed or self.reply is not None or self.state == "preparing":
            return
        self.release = self.downloaded = None
        self._state("checking", "正在检查正式发行版…")
        self._request(RELEASE_API, "metadata")

    def download(self):
        if self.closed or self.reply is not None or self.release is None or self.state == "preparing":
            return
        self.downloaded = None
        self.checksum = ""
        self.received = 0
        self._state("downloading", "正在获取安装包校验信息…")
        self.progress.emit(0, self.release.size)
        self._request(self.release.checksum_url, "checksum")

    def _request(self, url, kind, redirects=0):
        try:
            safe = allowed_request(url, kind)
        except ValueError:
            safe = False
        if not safe or redirects > 5:
            self._fail("更新下载地址不受信任或重定向次数过多。")
            return
        self.serial += 1
        serial = self.serial
        self.kind, self.redirects, self.buffer = kind, redirects, bytearray()
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(30000 if kind == "installer" else 15000)
        request.setRawHeader(b"User-Agent", f"CoinPilotAI/{self.current}".encode("ascii"))
        request.setRawHeader(b"Accept", b"application/vnd.github+json" if kind == "metadata" else b"application/octet-stream")
        reply = self.reply = self.manager.get(request)
        reply.setReadBufferSize(256*1024)
        reply.readyRead.connect(lambda: self._read(reply, serial))
        reply.finished.connect(lambda: self._finish(reply, serial))
        self.timeout.start(30*60*1000 if kind == "installer" else 20000)

    def _read(self, reply, serial):
        if self.closed or serial != self.serial or reply is not self.reply:
            return
        if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
            return
        try:
            while reply.bytesAvailable():
                raw = bytes(reply.read(256*1024))
                if not raw:
                    break
                if self.kind == "installer":
                    self.received += len(raw)
                    if self.received > self.release.size:
                        raise ValueError("下载内容超过发行版声明的安装包大小")
                    self.file.write(raw)
                    self.hasher.update(raw)
                else:
                    self.buffer.extend(raw)
                    limit = 2*1024*1024 if self.kind == "metadata" else 4096
                    if len(self.buffer) > limit:
                        raise ValueError("更新信息超出允许大小")
            if self.kind == "installer":
                self.progress.emit(self.received, self.release.size)
        except (OSError, ValueError) as exc:
            self._fail(str(exc) if isinstance(exc, ValueError) else "无法写入更新缓存，请检查磁盘空间与权限。")

    def _finish(self, reply, serial):
        if self.closed or serial != self.serial or reply is not self.reply:
            return
        self._read(reply, serial)
        if reply is not self.reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
        redirect = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
        network_error = reply.error() != QNetworkReply.NetworkError.NoError
        self.reply = None
        self.timeout.stop()
        reply.deleteLater()
        if status in (301, 302, 303, 307, 308) and redirect is not None:
            self._request(reply.url().resolved(redirect).toString(), self.kind, self.redirects+1)
            return
        if network_error or status != 200:
            message = ("未找到可用的公开正式发行版。" if status == 404 else
                       "发行源暂时限制请求，请稍后再试。" if status in (403, 429) else
                       f"更新请求失败（HTTP {status or '无响应'}），请检查网络或代理后重试。")
            self._fail(message)
            return
        try:
            if self.kind == "metadata":
                self.release = parse_release(json.loads(self.buffer), self.current)
                if self.release is None:
                    self._state("current", "当前已是最新正式版。")
                else:
                    self._state("available", f"发现新版本 {self.release.version}。")
                    self.available.emit(self.release)
            elif self.kind == "checksum":
                self.checksum = parse_checksum(bytes(self.buffer), self.release.filename)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                folder = Path(tempfile.mkdtemp(prefix="release-", dir=self.cache_dir))
                self.part = folder/(self.release.filename+".part")
                self.file = self.part.open("xb")
                self.hasher = hashlib.sha256()
                self._state("downloading", "正在后台下载安装包，可继续使用程序。")
                self._request(self.release.url, "installer")
            else:
                stream, self.file = self.file, None
                stream.close()
                if self.received != self.release.size or self.hasher.hexdigest() != self.checksum:
                    raise ValueError("安装包大小或 SHA-256 校验失败，请重新下载。")
                target = self.part.with_name(self.release.filename)
                self.part.replace(target)
                self.part = None
                self.downloaded = target
                self._state("ready", "安装包已下载并通过 SHA-256 校验。")
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            self._fail(str(exc) if isinstance(exc, ValueError) else "无法读取更新信息或保存安装包，请重试。")

    def _abort(self):
        self.serial += 1
        self.timeout.stop()
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        if self.file is not None:
            stream, self.file = self.file, None
            try:
                stream.close()
            except OSError:
                pass
        if self.part is not None:
            try:
                self.part.unlink(missing_ok=True)
            except OSError:
                pass
            self.part = None

    def _fail(self, message):
        self._abort()
        if not self.closed:
            self._state("error", message)

    def cancel(self):
        self._abort()
        self._state("available" if self.release else "idle", "已取消，可稍后重试。")

    def close(self):
        self.closed = True
        self._abort()
