"""离线渲染工作台三页及原迷你窗口，不访问用户配置、凭据或真实账户。"""
from pathlib import Path
from contextlib import ExitStack
import argparse
import json
import math
import os
import sys
import tempfile
import time
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtTest import QTest
from PyQt6.QtGui import QFontDatabase
from coinpilot_ai.application.bootstrap import create_application
from coinpilot_ai.core.config import DEFAULT_CONFIG, SettingsStore
from coinpilot_ai.desktop.widget import CoinPilotWidget
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.workbench.window import Workbench
from coinpilot_ai.charts.state import drawing
from coinpilot_ai.charts.dialogs import EmaDialog, DrawingDialog, ObjectsDialog


class EmptyVault:
    def read(self, key):
        return None


def verify_native_titlebar(window, output):
    """读取 Windows 命中结果，避免离线平台掩盖原生消息或坐标错误。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    send = user32.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_ssize_t
    hwnd = ctypes.c_void_p(int(window.winId()))

    def hit(point):
        rect = wintypes.RECT()
        assert user32.GetWindowRect(hwnd, ctypes.byref(rect))
        scale = window.devicePixelRatioF()
        x, y = rect.left + round(point.x()*scale), rect.top + round(point.y()*scale)
        return send(hwnd, 0x84, 0, (x & 0xffff) | ((y & 0xffff) << 16))

    from PyQt6.QtCore import QPoint
    assert hit(QPoint(1, 1)) == 13
    assert hit(QPoint(window.width()-1, window.height()-1)) == 17
    bar = window.title_bar
    drag = bar.caption.mapTo(window, bar.caption.rect().center())
    assert hit(drag) == 2
    for button in (bar.view_button, *bar.navigation, bar.maximize_button, bar.close_button):
        assert hit(button.mapTo(window, button.rect().center())) == 1
    normal = window.size()
    bar.maximize_button.click()
    QTest.qWait(80)
    assert window.isMaximized()
    assert window.screen().availableGeometry().contains(window.geometry())
    bar.maximize_button.click()
    QTest.qWait(80)
    assert not window.isMaximized() and window.size() == normal
    send(hwnd, 0xA3, 2, 0)
    QTest.qWait(80)
    assert window.isMaximized(), '原生标题栏双击应最大化'
    window.showNormal()
    QTest.qWait(80)
    (output / 'native-titlebar.txt').write_text(
        'PASS: edges, drag region, controls, maximize/restore, native double-click\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--native", action="store_true", help="用临时数据检查 Windows 原生停靠交互")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "workbench")
    args = parser.parse_args()
    if args.native:
        os.environ["QT_QPA_PLATFORM"] = "windows"
    if args.all:
        for scale in ("1", "1.25", "1.5", "1.75", "2"):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--output", str(args.output / scale)],
                env=dict(os.environ, QT_SCALE_FACTOR=scale), check=True, timeout=45)
        return
    app = create_application([])
    # Windows 的 offscreen 插件不会枚举系统字体，显式装入用于离线截图。
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and sys.platform == "win32":
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"]) / "Fonts" / name))
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="crypto-workbench-qa-") as folder, ExitStack() as cleanup:
        path = Path(folder)
        config = dict(DEFAULT_CONFIG, proxy_enabled=False)
        service = CockpitService(config, path / "workbench.db", path / "icons", vault=EmptyVault(), autostart=False)
        cleanup.callback(service.close)
        # 禁止渲染过程中由页面操作启动网络请求。
        service.refresh_market = lambda: None
        service.fetch_candles = lambda *_: None
        now = time.time()
        rows = []
        for i in range(300):
            price = 57900 + i*12 + math.sin(i/6)*230
            rows.append([str(int((now-(300-i)*900)*1000)), str(price), str(price+95), str(price-75), str(price+math.cos(i)*50), str(80+i%13*9), "0", "0", "1"])
        service.candles[(service.selected, "15m")] = rows
        service.candle_times[(service.selected, "15m")] = now
        service.chart_feed.stream_state = "实时（离线示例）"
        for inst, price in (("BTC-USDT-SWAP", f"{float(rows[-1][4]):.2f}"), ("ETH-USDT-SWAP", "3240.80"), ("SOL-USDT-SWAP", "145.32")):
            service.quotes[inst] = {"instrument": inst, "price": price, "source": "okx", "time": now}
        service.market_error = ""
        service.account = {"posMode": "net_mode", "time": now}
        service.balance = {"totalEq": "10000.00"}
        service.account_error = ""
        service.positions = [{"posId": "demo", "instId": service.selected, "posSide": "net", "pos": "2", "availPos": "2", "mgnMode": "cross", "avgPx": "61000", "upl": "8.41", "lever": "3"}]
        service.record_event({"name": "BTC 穿越价格观察线（示例）", "instrument": service.selected, "time": now, "source": "okx", "priority": "market", "evidence": [{"metric": "price", "value": "61420.50", "threshold": "61400"}]})
        widget = CoinPilotWidget(config, SettingsStore(path / "settings.json"), start_requests=False)
        cleanup.callback(widget.close)
        from coinpilot_ai.updates.ui import UpdateController
        updater = UpdateController(app, widget, path / "icons", automatic=False)
        cleanup.callback(updater.close)
        window = Workbench(service, settings_owner=widget, updater=updater)
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        if args.native:
            verify_native_titlebar(window, output)
        scale = window.devicePixelRatioF() if args.native else float(os.environ.get("QT_SCALE_FACTOR", "1"))
        assert abs(window.devicePixelRatioF()-scale) < .02
        assert window.width() <= 1440 and window.height() <= 900, "工作台最小尺寸不应挤大初始窗口"
        for index, name in enumerate(("workspace", "review", "settings")):
            window.pages.setCurrentIndex(index)
            window.refresh("account")
            window.refresh("candles")
            QTest.qWait(80)
            app.processEvents()
            window.grab().save(str(output / (name + ".png")))
        for section in window.settings_page.section_indices:
            window.open_settings(section)
            app.processEvents()
            window.grab().save(str(output / ("settings-" + str(window.settings_page.tabs.currentIndex()) + ".png")))
        window.pages.setCurrentIndex(0)
        objects = [drawing("trend", [[float(rows[-83][0]), float(rows[-83][3])-90], [float(rows[-18][0]), float(rows[-18][3])-90]])]
        fib = drawing("fib", [[float(rows[-53][0]), float(rows[-53][3])], [float(rows[-23][0]), float(rows[-23][2])+80]])
        for level, color in zip(fib["levels"], ("#8799b5", "#e16b79", "#d6a65e", "#6ac09b", "#53b7d1", "#9185d1", "#8799b5")):
            level["color"] = color
        objects.append(fib)
        service.chart_book.save_objects("demo", service.selected, objects)
        app.processEvents()
        window.grab().save(str(output / "monitor-tools.png"))
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        canvas = window.chart.canvas
        canvas.count = 100
        canvas.latest()
        app.processEvents()
        service.chart_book.save_objects("demo", service.selected, objects[:1])
        low_anchor = [float(rows[-65][0]), float(rows[-65][3])]
        high_anchor = [float(rows[-23][0]), float(rows[-23][2])]
        window.chart.choose_tool("fib")
        canvas.draw_click(canvas.point(low_anchor)+QPointF(2, -3))
        position = canvas.point(high_anchor)+QPointF(2, 3)
        canvas.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, position, position,
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        assert canvas.preview["anchors"] == [low_anchor, high_anchor], (canvas.preview["anchors"], low_anchor, high_anchor, canvas.width(), canvas.count, canvas.isVisible())
        assert canvas.snap_target is not None
        app.processEvents()
        window.grab().save(str(output / "chart-magnet.png"))
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        # 交叉线围成的封闭区域：填色、右键价格菜单和透明度属性。
        from unittest.mock import patch
        from PyQt6.QtGui import QColor
        main, _ = canvas.plot_rects()
        points = [QPointF(main.left()+main.width()*.2, main.top()+main.height()*.25),
                  QPointF(main.left()+main.width()*.8, main.top()+main.height()*.25),
                  QPointF(main.left()+main.width()*.5, main.top()+main.height()*.8)]
        canvas.objects = [drawing('trend', [canvas.anchor(a), canvas.anchor(b)])
                          for a, b in zip(points, points[1:]+points[:1])]
        canvas.persist_objects()
        center = sum(points, QPointF())/3
        with patch('coinpilot_ai.charts.canvas.QColorDialog.getColor', return_value=QColor('#4388ff')):
            canvas.fill_region(canvas.region_at(center))
        app.processEvents()
        window.grab().save(str(output / 'chart-region.png'))
        menu = canvas.context_menu(center)
        menu.popup(canvas.mapToGlobal(center.toPoint()))
        app.processEvents()
        menu.grab().save(str(output / 'chart-context-menu.png'))
        menu.close()
        menu.deleteLater()
        dialog = DrawingDialog(canvas.objects[-1], window)
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(output / 'chart-region-properties.png'))
        dialog.close()
        dialog.deleteLater()
        canvas.objects[0]['locked'] = True
        canvas.persist_objects()
        manager = ObjectsDialog(canvas, window)
        manager.show()
        manager.items.selectAll()
        app.processEvents()
        manager.grab().save(str(output / 'objects-multiselect.png'))
        manager.delete()
        assert len(canvas.objects) == 1 and canvas.objects[0]['locked']
        app.processEvents()
        manager.grab().save(str(output / 'objects-after-delete.png'))
        service.chart_book.undo('demo', service.selected)
        assert len(canvas.objects) == 4
        manager.close()
        manager.deleteLater()
        service.chart_book.save_objects("demo", service.selected, objects)
        window.chart.maximize()
        app.processEvents()
        window.grab().save(str(output / "chart-maximized.png"))
        window.chart.maximize()
        for dialog, name in ((EmaDialog(service.chart_book, window), "ema-settings"), (DrawingDialog(fib, window), "fib-settings")):
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(output / (name + ".png")))
            dialog.close()
            dialog.deleteLater()
        window.pages.setCurrentIndex(1)
        for index, name in ((1, "replay"), (2, "strategy")):
            window.review_tabs.setCurrentIndex(index)
            app.processEvents()
            window.grab().save(str(output / (name + ".png")))
        window.review_tabs.setCurrentIndex(0)
        window.pages.setCurrentIndex(0)
        window.multi_chart.layout_choice.setCurrentIndex(window.multi_chart.layout_choice.findData(6))
        app.processEvents()
        window.grab().save(str(output / "six-charts.png"))
        window.multi_chart.layout_choice.setCurrentIndex(window.multi_chart.layout_choice.findData(1))
        app.processEvents()
        assert window.chart.geometry() == window.multi_chart.grid_host.contentsRect(), "多图切回单图应铺满盘面"
        window.grab().save(str(output / "single-chart-restored.png"))
        window.resize(1000, 650)
        app.processEvents()
        assert window.width() <= 1000 and window.height() <= 650, "最小工作台尺寸应可用"
        window.pages.setCurrentIndex(0)
        app.processEvents()
        assert window.chart.canvas.width() >= 260 and window.chart.canvas.height() >= 180
        submit = window.trade.submit_button
        assert submit.isVisible() and submit.mapTo(window, submit.rect().bottomLeft()).y() < window.height() - 20
        window.grab().save(str(output / "compact-workspace.png"))
        for key, dock in window.workspace.docks.items():
            dock.setFloating(True)
            app.processEvents()
            dock.grab().save(str(output / ("floating-" + key + ".png")))
        window.pages.setCurrentIndex(1)
        assert all(not d.isVisible() for d in window.workspace.docks.values())
        window.pages.setCurrentIndex(0)
        app.processEvents()
        assert all(d.isVisible() for d in window.workspace.docks.values())
        window.workspace.reset()
        if args.native:
            from PyQt6.QtCore import QPoint, Qt
            dock = window.workspace.docks["market"]
            QTest.qWait(80)
            QTest.mouseDClick(dock, Qt.MouseButton.LeftButton, pos=QPoint(80, 12))
            QTest.qWait(80)
            assert dock.isFloating(), "原生标题栏双击应浮动面板"
            # 浮动窗口通过原生标题栏移动；使用 Qt 鼠标事件验证移动不会重建内容。
            original_chart = window.chart
            dock.move(dock.x()+40, dock.y()+30)
            QTest.qWait(80)
            assert window.chart is original_chart
            dock.grab().save(str(output / "native-floating.png"))
            dock.setFloating(False)
            QTest.qWait(80)
            assert not dock.isFloating()
        widget.show()
        widget.set_unread_events(1)
        app.processEvents()
        widget.grab().save(str(output / "mini.png"))
        assert widget.logical_height == 28
        (output / "report.json").write_text(json.dumps({"scale": scale, "mini_height": widget.logical_height,
            "compact_size": [window.width(), window.height()], "chart_width": window.chart.canvas.width()}, indent=2), encoding="utf-8")
        window.close()
        assert not service.closed
        from unittest.mock import patch
        from coinpilot_ai.desktop.controller import DesktopController
        from PyQt6.QtCore import QPoint
        with patch("coinpilot_ai.desktop.controller.CockpitService", return_value=service):
            controller = DesktopController(app, widget, path / "settings.json", path / "icons")
        controller.window = window
        controller.menu.popup(QPoint(40, 40))
        app.processEvents()
        controller.menu.grab().save(str(output / "tray-menu.png"))
        controller.menu.hide()
        controller.toast.display("行情提醒 · BTC 价格穿越", "BTC-USDT-SWAP · 点击查看触发依据与 AI 解读", widget.screen())
        app.processEvents()
        controller.toast.grab().save(str(output / "notification.png"))
        controller.close()
        controller.deleteLater()
        widget.close()
        app.processEvents()
    print("工作台三页与 28px 迷你窗口已离线渲染：" + str(output))


if __name__ == "__main__":
    main()
