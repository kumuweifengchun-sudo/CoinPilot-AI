"""更新使用本地 HTTP 和临时文件验证；不下载公网安装器、不执行安装。"""
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget

from coinpilot_ai import updater, update_ui
from coinpilot_ai.config import DEFAULT_CONFIG, SettingsStore, validate_config
from coinpilot_ai.updater import Release, UpdateClient, parse_checksum, parse_release, version_key


def release_payload(version="0.2.0", body=b"MZ-test-installer"):
    name = f"CoinPilotAI-Setup-{version}-x64.exe"
    base = f"https://github.com/{updater.REPOSITORY}/releases/download/v{version}/"
    return {"tag_name": "v"+version, "draft": False, "prerelease": False, "body": "修复图表卡顿",
            "assets": [{"name": name, "size": len(body), "browser_download_url": base+name},
                       {"name": name+".sha256", "size": 120, "browser_download_url": base+name+".sha256"}]}


def wait_for(app, predicate, seconds=4):
    deadline = time.monotonic()+seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate(), "更新任务未在限定时间内完成"


@pytest.mark.parametrize("version,newer", [("0.1.0", False), ("0.1.0.0", False), ("0.0.9", False),
                                         ("0.1.0.1", True), ("0.2.0", True), ("0.10.0", True)])
def test_stable_numeric_version_selection(version, newer):
    assert (parse_release(release_payload(version), "0.1.0") is not None) is newer
    assert version_key("0.10.0") > version_key("0.2.0")


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_unstable_releases_are_ignored(field):
    payload = release_payload()
    payload[field] = True
    assert parse_release(payload) is None


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(tag_name="v0.2.0-beta"),
    lambda p: p.update(tag_name="../../evil"),
    lambda p: p.update(tag_name="v65536.0.0"),
    lambda p: p.update(assets=p["assets"][:1]),
    lambda p: p["assets"][0].update(size=True),
    lambda p: p["assets"][0].update(size=updater.MAX_INSTALLER+1),
    lambda p: p["assets"][0].update(browser_download_url="https://evil.invalid/setup.exe"),
    lambda p: p["assets"][1].update(name="other.sha256"),
    lambda p: p["assets"].append(p["assets"][0]),
])
def test_rejects_invalid_or_incomplete_release(mutation):
    payload = release_payload()
    mutation(payload)
    with pytest.raises(ValueError):
        parse_release(payload)


@pytest.mark.parametrize("url", ["http://github.com/x", "file:///C:/setup.exe", "https://github.com.evil.invalid/x",
                                 "https://user:pass@github.com/x", "https://release-assets.githubusercontent.com:444/x",
                                 "https://github.com/other/repo/releases/download/v1/setup.exe"])
def test_redirect_allowlist(url):
    assert not updater.allowed_request(url, "installer")
    assert updater.allowed_request("https://release-assets.githubusercontent.com/asset?signature=x", "installer")
    assert not updater.allowed_request("https://release-assets.githubusercontent.com/asset", "metadata")


def test_checksum_must_name_exact_installer():
    digest, filename = "a"*64, "CoinPilotAI-Setup-0.2.0-x64.exe"
    assert parse_checksum(f"{digest}  {filename}\r\n".encode(), filename) == digest
    for raw in (f"{digest}  other.exe".encode(), digest.encode(), b"garbage", b"\xff"):
        with pytest.raises(ValueError):
            parse_checksum(raw, filename)


@pytest.fixture
def release_server(app, tmp_path):
    binary = b"MZ"+b"offline update test"*60000
    payload = release_payload(body=binary)
    filename = payload["assets"][0]["name"]
    checksum = hashlib.sha256(binary).hexdigest()
    state = {"/release": (200, {}, json.dumps(payload).encode()),
             "/sha": (200, {}, f"{checksum}  {filename}\n".encode()),
             "/download": (302, {"Location": "https://release-assets.githubusercontent.com/test"}, b""),
             "/binary": (200, {}, binary), "delay": 0}
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, dict(self.headers)))
            status, headers, data = state[self.path]
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            try:
                for i in range(0, len(data), 32768):
                    self.wfile.write(data[i:i+32768])
                    self.wfile.flush()
                    if self.path == "/binary" and state["delay"]:
                        time.sleep(state["delay"])
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    routes = {updater.RELEASE_API: "/release", payload["assets"][0]["browser_download_url"]: "/download",
              payload["assets"][1]["browser_download_url"]: "/sha", "https://release-assets.githubusercontent.com/test": "/binary"}

    class LocalManager(QNetworkAccessManager):
        def get(self, request):
            # 只在测试中将已校验的生产 URL 映射到本机 HTTP；生产下载器没有禁用 TLS 的选项。
            request = QNetworkRequest(request)
            request.setUrl(QUrl(f"http://127.0.0.1:{server.server_port}"+routes[request.url().toString()]))
            return super().get(request)

    manager = LocalManager()
    client = UpdateClient(tmp_path/"updates", {"proxy_enabled": False}, manager=manager, current="0.1.0")
    yield client, state, seen, binary, checksum
    client.close()
    client.deleteLater()
    manager.deleteLater()
    app.processEvents()
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def check_available(app, client):
    client.check()
    wait_for(app, lambda: client.state == "available")


def test_real_qt_streaming_checksum_and_trusted_redirect(app, release_server):
    client, _, seen, binary, digest = release_server
    check_available(app, client)
    progress = []
    client.progress.connect(lambda *args: progress.append(args))
    client.download()
    wait_for(app, lambda: client.state == "ready")
    assert client.downloaded.read_bytes() == binary and client.checksum == digest
    assert progress[-1] == (len(binary), len(binary))
    assert not list(client.cache_dir.rglob("*.part"))
    assert [path for path, _ in seen] == ["/release", "/sha", "/download", "/binary"]
    assert all("Authorization" not in headers for _, headers in seen)


@pytest.mark.parametrize("failure", ["checksum", "short", "large", "redirect", "network"])
def test_failed_download_cannot_become_installable(app, release_server, failure):
    client, state, _, binary, _ = release_server
    check_available(app, client)
    if failure == "checksum":
        state["/binary"] = (200, {}, b"XX"+binary[2:])
    elif failure == "short":
        state["/binary"] = (200, {}, binary[:-1])
    elif failure == "large":
        state["/binary"] = (200, {}, binary+b"extra")
    elif failure == "redirect":
        state["/download"] = (302, {"Location": "https://evil.invalid/installer"}, b"")
    else:
        state["/binary"] = (503, {}, b"do not display raw response")
    client.download()
    wait_for(app, lambda: client.state == "error")
    assert client.downloaded is None and client.file is None
    assert not list(client.cache_dir.rglob("*.part"))
    assert "do not display" not in client.message


@pytest.mark.parametrize("action", ["cancel", "proxy", "close", "timeout"])
def test_inflight_cancel_proxy_close_and_timeout_ignore_late_callbacks(app, release_server, action):
    client, state, _, _, _ = release_server
    state["delay"] = .01
    check_available(app, client)
    client.download()
    wait_for(app, lambda: client.received > 0)
    if action == "cancel":
        client.cancel()
    elif action == "proxy":
        client.set_proxy(dict(DEFAULT_CONFIG, proxy_enabled=True))
    elif action == "close":
        client.close()
    else:
        client.timeout.start(1)
        wait_for(app, lambda: client.state == "error")
    QTest.qWait(150)
    assert client.downloaded is None and client.reply is None and client.file is None
    assert not list(client.cache_dir.rglob("*.part"))


def test_rate_limit_and_retry(app, release_server):
    client, state, _, _, _ = release_server
    original = state["/release"]
    state["/release"] = (403, {}, b"limited")
    client.check()
    wait_for(app, lambda: client.state == "error")
    assert "限制" in client.message
    state["/release"] = original
    check_available(app, client)


class Owner(QWidget):
    configuration_changed = pyqtSignal(object)
    update_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.config = dict(DEFAULT_CONFIG, proxy_enabled=False)


class ApplicationStub(QObject):
    aboutToQuit = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.quits = 0

    def quit(self):
        self.quits += 1


def test_automatic_check_preference_and_no_automatic_download(app, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    owner, application = Owner(), ApplicationStub()
    controller = update_ui.UpdateController(application, owner, tmp_path)
    notices, checks = [], []
    controller.notice.connect(notices.append)
    controller.client.check = lambda: checks.append(True)
    try:
        assert controller.check_timer.isActive() and controller.check_timer.interval() == 15000
        controller.auto_check()
        assert checks == [True] and controller.check_timer.interval() == controller.CHECK_INTERVAL
        release = parse_release(release_payload())
        controller.client.available.emit(release)
        controller.client.available.emit(release)
        assert len(notices) == 1 and controller.dialog is None and controller.client.reply is None
        owner.config["auto_check_updates"] = False
        controller.configure(owner.config)
        controller.auto_check()
        assert not controller.check_timer.isActive() and checks == [True]
        controller.open()
        assert checks == [True, True]  # 关闭自动检查不影响手动检查。
    finally:
        controller.close()
        owner.deleteLater()
        application.deleteLater()
        app.processEvents()


def test_update_preference_roundtrip_and_validation(tmp_path):
    store = SettingsStore(tmp_path/"settings.json")
    store.save(dict(DEFAULT_CONFIG, auto_check_updates=False))
    assert store.load()["auto_check_updates"] is False
    config, bad = validate_config({"auto_check_updates": "false"})
    assert config["auto_check_updates"] is True and bad == ["auto_check_updates"]


def test_release_notes_are_plain_text_and_source_cannot_install(app, tmp_path):
    owner, application = Owner(), ApplicationStub()
    controller = update_ui.UpdateController(application, owner, tmp_path, automatic=False)
    controller.client.release = Release("0.2.0", "x.exe", "", "", 10, '<img src="https://evil.invalid/x">')
    controller.client._state("available", "测试发行版")
    controller.open()
    try:
        assert controller.dialog.notes.toPlainText().startswith("<img")
        controller.client._state("ready", "已校验")
        assert not controller.dialog.action.isEnabled()
        controller.prepare_install()
        assert controller.helper is None and application.quits == 0
    finally:
        controller.close()
        owner.deleteLater()
        application.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("outcome", ["ready", "failed", "busy"])
def test_install_waits_for_verified_helper_before_exit(app, tmp_path, monkeypatch, outcome):
    owner, application = Owner(), ApplicationStub()
    controller = update_ui.UpdateController(application, owner, tmp_path, automatic=False)
    controller.install_supported = True
    controller.client.downloaded = tmp_path/"setup.exe"
    controller.client.checksum = "a"*64
    controller.client._state("ready", "已校验")
    ready, error = tmp_path/"ready", tmp_path/"error"
    calls = []
    process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(update_ui, "launch_installer_helper", lambda *args: (calls.append(args) or (process, ready, error)))
    try:
        if outcome == "busy":
            application.desktop = SimpleNamespace(service=SimpleNamespace(transport=SimpleNamespace(pending={"order": (None, None, "POST")})))
        controller.prepare_install()
        assert application.quits == 0
        if outcome == "busy":
            assert not calls and controller.helper is None
            return
        assert controller.client.state == "preparing" and len(calls) == 1
        controller.poll_helper()
        assert application.quits == 0
        (ready if outcome == "ready" else error).write_text("test")
        controller.poll_helper()
        assert application.quits == (1 if outcome == "ready" else 0)
        assert not controller.helper_timer.isActive()
    finally:
        controller.close()
        owner.deleteLater()
        application.deleteLater()
        app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 安装交接助手")
def test_real_helper_locks_verified_file_and_waits_without_installing(tmp_path):
    installer = tmp_path/"离线 测试.exe"
    installer.write_bytes(b"not an executable; must never be launched")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    process, ready, error = update_ui.launch_installer_helper(installer, digest, tmp_path)
    try:
        deadline = time.monotonic()+10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists() and not error.exists()
        assert process.poll() is None  # 当前测试进程仍运行，助手不得执行安装文件。
        with pytest.raises(PermissionError):
            installer.write_bytes(b"tampered")
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
