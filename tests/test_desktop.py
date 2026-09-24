"""桌面行为通过真实本地 IPC、多进程竞争及隔离的 Qt 界面验证。"""
from pathlib import Path
import sys
import uuid

from PyQt6.QtCore import QProcess, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QSystemTrayIcon

from coinpilot_ai.config import DEFAULT_CONFIG, SettingsStore
from coinpilot_ai.icons import ALIASES, CUSTOM, icon
from coinpilot_ai.single_instance import SingleInstance
from coinpilot_ai.widget import CoinPilotWidget


CHILD = '''
import sys
from PyQt6.QtCore import QCoreApplication,QTimer
from coinpilot_ai.single_instance import SingleInstance
app=QCoreApplication([])
guard=SingleInstance(namespace=sys.argv[1])
if guard.acquire(sys.argv[2]):
    print('PRIMARY',flush=True)
    guard.set_handler(lambda command: print('COMMAND:'+command,flush=True))
    QTimer.singleShot(int(sys.argv[3]),app.quit)
    app.exec()
    guard.close()
else:
    print('SECONDARY',flush=True)
'''


def child(namespace, command="show", duration=2500):
    process = QProcess()
    process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    process.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
    process.start(sys.executable, ["-u", "-c", CHILD, namespace, command, str(duration)])
    assert process.waitForStarted(3000)
    return process


def test_concurrent_launch_has_one_owner_and_routes_commands(app):
    namespace = "test-" + uuid.uuid4().hex
    children = [child(namespace, command) for command in ("show", "workbench", "settings", "show")]
    outputs = []
    try:
        for process in children:
            assert process.waitForFinished(8000)
            output = bytes(process.readAll()).decode()
            assert process.exitCode() == 0, output
            outputs.append(output)
        assert sum("PRIMARY" in value for value in outputs) == 1
        assert sum("SECONDARY" in value for value in outputs) == 3
        assert sum(value.count("COMMAND:") for value in outputs) == 3
    finally:
        for process in children:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(2000)


def test_dead_owner_lock_is_recovered(app):
    namespace = "test-" + uuid.uuid4().hex
    process = child(namespace, duration=30000)
    try:
        assert process.waitForReadyRead(4000)
        assert b"PRIMARY" in bytes(process.readAll())
    finally:
        process.kill()
        process.waitForFinished(3000)
    guard = SingleInstance(namespace=namespace)
    try:
        assert guard.acquire()
    finally:
        guard.close()
    # 正常退出也应马上允许下一次启动。
    replacement = SingleInstance(namespace=namespace)
    assert replacement.acquire()
    replacement.close()


def test_icon_pack_all_modes_and_dpi(app):
    names = set(ALIASES) | set(CUSTOM) | {p.stem for p in Path("coinpilot_ai/assets/lucide").glob("*.svg")}
    for name in names:
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled, QIcon.Mode.Selected):
            pixmap = icon(name).pixmap(QSize(18, 18), 2., mode)
            assert not pixmap.isNull()
            assert pixmap.size() == QSize(36, 36)
            assert pixmap.devicePixelRatioF() == 2


def test_tray_restore_pause_badge_and_service_fallback(app, tmp_path, monkeypatch):
    from coinpilot_ai.cockpit import desktop
    from coinpilot_ai.cockpit.service import CockpitService
    class EmptyVault:
        def read(self, key):
            return None
    service = CockpitService(dict(DEFAULT_CONFIG), tmp_path / "test.db", tmp_path / "icons", vault=EmptyVault(), autostart=False)
    monkeypatch.setattr(desktop, "CockpitService", lambda *args, **kwargs: service)
    widget = CoinPilotWidget(dict(DEFAULT_CONFIG), SettingsStore(tmp_path / "settings.json"), start_requests=False)
    controller = desktop.DesktopController(app, widget, widget.store.path, tmp_path / "icons")
    try:
        widget.hide()
        controller.activated(QSystemTrayIcon.ActivationReason.Trigger)
        QTest.qWait(app.doubleClickInterval()+30)
        assert widget.isVisible()
        assert controller.visibility_action.text() == "隐藏迷你窗口"
        controller.pause_action.setChecked(True)
        assert service.settings["notifications_paused"]
        controller.notify("x", {"name": "提醒", "priority": "market"})
        assert "1 条未读提醒" in controller.tray.toolTip()
        assert not controller.toast.isVisible()
        controller.activated(QSystemTrayIcon.ActivationReason.DoubleClick)
        assert controller.window.isVisible()
        controller.window.showMinimized()
        controller.open_workbench()
        assert not controller.window.isMinimized()
        assert widget.unread_events == 0
        controller.window.close()
        widget.hide()
        assert not service.closed
    finally:
        controller.close()
        controller.deleteLater()
        widget.close()
        widget.deleteLater()
        app.processEvents()
    def fail(*args, **kwargs):
        raise OSError("数据库无法打开")
    monkeypatch.setattr(desktop, "CockpitService", fail)
    fallback = CoinPilotWidget(dict(DEFAULT_CONFIG), SettingsStore(tmp_path / "fallback.json"), start_requests=False)
    controller = desktop.DesktopController(app, fallback, fallback.store.path, tmp_path / "icons")
    try:
        assert controller.tray.isVisible() and controller.service is None
        assert controller.warning and not controller.workbench_action.isEnabled()
        controller.show_mini()
        assert fallback.isVisible()
    finally:
        controller.close()
        controller.deleteLater()
        fallback.close()
        fallback.deleteLater()
        app.processEvents()
