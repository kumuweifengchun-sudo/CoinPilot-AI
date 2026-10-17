"""Windows TLS 初始化隔离外部 OpenSSL，同时保留原进程环境。"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from coinpilot_ai.application import bootstrap


@pytest.mark.parametrize("previous_path", [None, "foreign-openssl;developer-tools"])
@pytest.mark.parametrize("result", [True, False, "error"])
def test_windows_tls_restores_path_after_backend_selection(monkeypatch, previous_path, result):
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    if previous_path is None:
        monkeypatch.delenv("PATH", raising=False)
    else:
        monkeypatch.setenv("PATH", previous_path)
    selected = []

    def select_backend(name: str) -> bool:
        selected.append(name)
        assert os.environ["PATH"] == str(Path("C:/Windows") / "System32")
        if result == "error":
            raise OSError("backend load failed")
        return bool(result)

    monkeypatch.setattr(bootstrap.QSslSocket, "setActiveBackend", select_backend)
    if result is True:
        bootstrap.configure_windows_tls()
    elif result is False:
        with pytest.raises(RuntimeError, match="Schannel"):
            bootstrap.configure_windows_tls()
    else:
        with pytest.raises(OSError, match="backend load failed"):
            bootstrap.configure_windows_tls()
    assert selected == ["schannel"]
    assert os.environ.get("PATH") == previous_path


def test_windows_tls_rejects_missing_system_directory(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    monkeypatch.delenv("SystemRoot", raising=False)
    previous_path = os.environ.get("PATH")
    with pytest.raises(RuntimeError, match="系统目录"):
        bootstrap.configure_windows_tls()
    assert os.environ.get("PATH") == previous_path


def test_non_windows_keeps_platform_tls_backend(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")

    def unexpected_selection(name: str) -> bool:
        raise AssertionError(f"Unexpected backend selection: {name}")

    monkeypatch.setattr(bootstrap.QSslSocket, "setActiveBackend", unexpected_selection)
    bootstrap.configure_windows_tls()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Schannel regression")
def test_native_tls_initialization_and_requests_have_no_loader_exception(tmp_path):
    fault_log = tmp_path / "tls.fault.log"
    script = """
import faulthandler, os, sys
fault = open(sys.argv[1], 'w', encoding='utf-8')
faulthandler.enable(file=fault)
from coinpilot_ai.application.bootstrap import create_application
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QSslSocket
from PyQt6.QtWebSockets import QWebSocket
previous_path = os.environ.get('PATH')
app = create_application([])
assert os.environ.get('PATH') == previous_path
assert QSslSocket.activeBackend() == 'schannel'
assert QSslSocket.supportsSsl()
manager = QNetworkAccessManager()
reply = manager.get(QNetworkRequest(QUrl('https://127.0.0.1:1')))
socket = QWebSocket()
socket.open(QUrl('wss://127.0.0.1:1'))
QTimer.singleShot(300, app.quit)
app.exec()
socket.abort()
reply.abort()
print('schannel-ready')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(fault_log)],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "schannel-ready" in result.stdout
    assert fault_log.read_text(encoding="utf-8") == ""
