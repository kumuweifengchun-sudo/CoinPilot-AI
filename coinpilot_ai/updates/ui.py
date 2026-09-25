"""更新界面与应用退出交接。自动检查不下载、不弹出模态窗口、不重启应用。"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox,
                            QProgressBar, QPushButton, QTextEdit, QVBoxLayout)

from coinpilot_ai.ui.theme import events, style_sheet
from .client import UpdateClient
from coinpilot_ai.core.version import VERSION
from coinpilot_ai.core.paths import resource_path


def launch_installer_helper(installer, checksum, install_dir):
    """独立的本地助手等待原进程结束；参数始终以列表传递，不拼接 shell 命令。"""
    installer, install_dir = Path(installer).resolve(), Path(install_dir).resolve()
    token = uuid.uuid4().hex
    script = installer.parent/f"install-{token}.ps1"
    ready, error = installer.parent/f"{token}.ready", installer.parent/f"{token}.error"
    shutil.copyfile(resource_path("coinpilot_ai/assets/update-install.ps1"), script)
    powershell = Path(os.environ["SystemRoot"])/"System32/WindowsPowerShell/v1.0/powershell.exe"
    process = subprocess.Popen([
        str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass", "-File", str(script), "-ParentPid", str(os.getpid()),
        "-Installer", str(installer), "-ExpectedHash", checksum, "-InstallDir", str(install_dir),
        "-ReadyFile", str(ready), "-ErrorFile", str(error),
    ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    return process, ready, error


class UpdateDialog(QDialog):
    def __init__(self, controller, parent=None, *, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.controller, self.client = controller, controller.client
        self.setWindowTitle("CoinPilot AI · 软件更新")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, not embedded)
        self.resize(500, 360)
        self.setMinimumWidth(340)
        self.setStyleSheet(style_sheet())
        events.changed.connect(self.apply_theme)
        root = QVBoxLayout(self)
        if embedded:
            title = QLabel("软件更新")
            title.setObjectName("title")
            root.addWidget(title)
        self.version = QLabel(f"当前版本：{VERSION}")
        root.addWidget(self.version)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName("更新说明")
        root.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        if not controller.install_supported:
            hint = QLabel("当前为源码运行，可检查和下载发行版；自动安装仅在打包程序中可用。")
            hint.setWordWrap(True)
            root.addWidget(hint)
        if embedded:
            root.addStretch(1)
        buttons = QHBoxLayout()
        self.check = QPushButton("检查更新")
        self.check.clicked.connect(self.client.check)
        self.action = QPushButton()
        self.action.clicked.connect(self.act)
        self.cancel = QPushButton("取消下载")
        self.cancel.clicked.connect(self.client.cancel)
        self.later = QPushButton("稍后处理")
        self.later.clicked.connect(self.close)
        for button in (self.check, self.action, self.cancel, self.later):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        root.addLayout(buttons)
        if embedded:
            self.later.hide()
        self._notes_version = None
        self.client.changed.connect(self.refresh)
        self.client.progress.connect(self.update_progress)
        self.refresh()

    def update_progress(self, received, total):
        self.progress.setValue(round(received/total*100) if total else 0)
        self.progress.setFormat(f"{received/1048576:.1f} / {total/1048576:.1f} MB · %p%")

    def apply_theme(self, _theme_id):
        self.setStyleSheet(style_sheet())

    def refresh(self, *_):
        c = self.client
        release = c.release
        self.version.setText(f"当前版本：{VERSION}"+(f"    最新版本：{release.version}" if release else ""))
        self.status.setText(c.message)
        if release and release.version != self._notes_version:
            self.notes.setPlainText(release.notes)
            self._notes_version = release.version
        self.notes.setVisible(release is not None)
        busy = c.state in ("checking", "downloading", "preparing")
        self.check.setEnabled(not busy)
        self.action.setVisible(release is not None)
        self.action.setText("退出并安装" if c.state == "ready" else "下载更新")
        self.action.setEnabled(not busy and (c.state != "ready" or self.controller.install_supported))
        self.cancel.setVisible(c.state in ("checking", "downloading"))
        self.cancel.setText("取消检查" if c.state == "checking" else "取消下载")
        self.later.setEnabled(c.state != "preparing")
        self.progress.setVisible(c.state in ("downloading", "ready"))
        if c.state == "ready":
            self.update_progress(release.size, release.size)
        elif c.state == "downloading" and release:
            self.update_progress(c.received, release.size)

    def act(self):
        if self.client.state == "ready":
            self.controller.confirm_install()
        else:
            self.client.download()

    def closeEvent(self, event):
        if self.client.state == "preparing":
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        if self.embedded:
            return
        if self.client.state != "preparing":
            super().reject()


class UpdateController(QObject):
    notice = pyqtSignal(str)
    CHECK_INTERVAL = 6 * 60 * 60 * 1000

    def __init__(self, app, widget, cache_dir, *, automatic=True, client=None, parent=None):
        super().__init__(parent or app)
        self.app, self.widget = app, widget
        self.client = client or UpdateClient(Path(cache_dir)/"updates", widget.config, self)
        self.install_supported = sys.platform == "win32" and bool(getattr(sys, "frozen", False))
        self.automatic = automatic and self.install_supported
        self.dialog = None
        self.settings_router = None
        self.notified = None
        self.closed = False
        self.helper = None
        self.install_window = None
        self.check_timer = QTimer(self)
        self.check_timer.setSingleShot(True)
        self.check_timer.timeout.connect(self.auto_check)
        self.helper_timer = QTimer(self)
        self.helper_timer.setInterval(100)
        self.helper_timer.timeout.connect(self.poll_helper)
        self.client.available.connect(self.on_available)
        widget.configuration_changed.connect(self.configure)
        widget.update_requested.connect(self.open)
        app.aboutToQuit.connect(self.close)
        self.configure(widget.config)

    def configure(self, config):
        if self.closed:
            return
        self.client.set_proxy(config)
        if self.automatic and config.get("auto_check_updates", True):
            if not self.check_timer.isActive():
                self.check_timer.start(15000)
        else:
            self.check_timer.stop()

    def auto_check(self):
        if self.closed or not self.automatic or not self.widget.config.get("auto_check_updates", True):
            return
        if self.client.state not in ("checking", "downloading", "ready", "preparing"):
            self.client.check()
        self.check_timer.start(self.CHECK_INTERVAL)

    def on_available(self, release):
        if release.version != self.notified:
            self.notified = release.version
            self.notice.emit(f"发现 CoinPilot AI {release.version}，点击查看更新说明和下载。")

    def open(self):
        if self.closed:
            return
        if self.settings_router is not None:
            self.settings_router()
            if self.client.state in ("idle", "current"):
                self.client.check()
            return
        if self.dialog is None:
            self.dialog = UpdateDialog(self, QApplication.activeModalWidget() or self.widget)
            self.dialog.destroyed.connect(self.dialog_destroyed)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        if self.client.state in ("idle", "current"):
            self.client.check()

    def dialog_destroyed(self):
        self.dialog = None

    def trade_busy(self):
        desktop = getattr(self.app, "desktop", None)
        service = getattr(desktop, "service", None)
        return service is not None and any(method != "GET" for _, _, method in service.transport.pending.values())

    def confirm_install(self):
        if not self.install_supported or self.client.state != "ready":
            return
        if self.trade_busy():
            QMessageBox.information(self.dialog, "请稍后安装", "交易请求正在处理中，请等待结果返回后再安装更新。")
            return
        message = QMessageBox(self.dialog)
        message.setWindowTitle("退出并安装更新")
        message.setText("将退出 CoinPilot AI 并打开新版安装向导。退出期间行情监控和本地提醒会暂停，个人设置和记录会保留。")
        install = message.addButton("退出并安装", QMessageBox.ButtonRole.AcceptRole)
        later = message.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        message.setDefaultButton(later)
        message.exec()
        if message.clickedButton() is install:
            self.prepare_install()

    def prepare_install(self):
        if not self.install_supported or self.client.state != "ready" or self.trade_busy():
            return
        try:
            self.helper = launch_installer_helper(self.client.downloaded, self.client.checksum, Path(sys.executable).parent)
        except (OSError, KeyError, ValueError):
            self.client._state("ready", "无法启动安装助手，程序仍在运行，请稍后重试。")
            return
        self.client._state("preparing", "正在准备安装；助手就绪后程序将正常退出…")
        if self.dialog is not None:
            if getattr(self.dialog, "embedded", False):
                self.install_window = self.dialog.window()
                self.install_window.setEnabled(False)
            else:
                self.dialog.hide()
                self.dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
                self.dialog.show()
        self.helper_deadline = time.monotonic()+30
        self.helper_timer.start()

    def poll_helper(self):
        if self.closed or self.helper is None:
            return
        process, ready, error = self.helper
        if error.exists() or process.poll() is not None:
            self.helper_failed()
        elif ready.exists():
            self.helper_timer.stop()
            self.app.quit()
        elif time.monotonic() >= self.helper_deadline:
            try:
                process.terminate()  # 仅停止本次启动、尚未就绪的助手。
            except OSError:
                pass
            self.helper_failed()

    def helper_failed(self):
        self.helper_timer.stop()
        self.helper = None
        if self.install_window is not None:
            self.install_window.setEnabled(True)
            self.install_window = None
        if self.dialog is not None:
            if not getattr(self.dialog, "embedded", False):
                self.dialog.hide()
                self.dialog.setWindowModality(Qt.WindowModality.NonModal)
                self.dialog.show()
        self.client._state("ready", "安装助手未能就绪，程序继续运行。请检查安装包或系统权限后重试。")

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.check_timer.stop()
        self.helper_timer.stop()
        self.client.close()
        if self.dialog is not None:
            self.dialog.hide()
            self.dialog.deleteLater()
            self.dialog = None
