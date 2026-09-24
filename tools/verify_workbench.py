"""离线渲染工作台四页及原迷你窗口，不访问用户配置、凭据或真实账户。"""
from pathlib import Path
import argparse
import json
import math
import os
import sys
import tempfile
import time
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtTest import QTest
from PyQt6.QtGui import QFontDatabase
from coinpilot_ai.app import create_application
from coinpilot_ai.config import DEFAULT_CONFIG, SettingsStore
from coinpilot_ai.widget import CoinPilotWidget
from coinpilot_ai.cockpit.service import CockpitService
from coinpilot_ai.cockpit.workbench import Workbench
from coinpilot_ai.cockpit.chart_state import drawing
from coinpilot_ai.cockpit.chart_dialogs import EmaDialog, DrawingDialog


class EmptyVault:
    def read(self, key):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "workbench")
    args = parser.parse_args()
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
    with tempfile.TemporaryDirectory(prefix="crypto-workbench-qa-") as folder:
        path = Path(folder)
        config = dict(DEFAULT_CONFIG, proxy_enabled=False)
        service = CockpitService(config, path / "workbench.db", path / "icons", vault=EmptyVault(), autostart=False)
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
        window = Workbench(service, lambda: None)
        window.show()
        app.processEvents()
        scale = float(os.environ.get("QT_SCALE_FACTOR", "1"))
        assert abs(window.devicePixelRatioF()-scale) < .02
        assert window.width() <= 1200 and window.height() <= 720, "工作台最小尺寸不应挤大初始窗口"
        for index, name in enumerate(("monitor", "trade", "review", "settings")):
            window.pages.setCurrentIndex(index)
            window.refresh("account")
            window.refresh("candles")
            QTest.qWait(80)
            app.processEvents()
            window.grab().save(str(output / (name + ".png")))
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
        service.chart_book.save_objects("demo", service.selected, objects[:1])
        low_anchor = [float(rows[-65][0]), float(rows[-65][3])]
        high_anchor = [float(rows[-23][0]), float(rows[-23][2])]
        window.chart.choose_tool("fib")
        canvas.draw_click(canvas.point(low_anchor)+QPointF(2, -3))
        position = canvas.point(high_anchor)+QPointF(2, 3)
        canvas.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, position, position,
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        assert canvas.preview["anchors"] == [low_anchor, high_anchor]
        assert canvas.snap_target is not None
        app.processEvents()
        window.grab().save(str(output / "chart-magnet.png"))
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
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
        window.resize(1000, 650)
        app.processEvents()
        assert window.width() <= 1000 and window.height() <= 650, "最小工作台尺寸应可用"
        for index in (0, 1):
            window.pages.setCurrentIndex(index)
            app.processEvents()
            panel = window.chart if index == 0 else window.trade.chart
            assert panel.canvas.width() >= 300 and panel.canvas.height() >= 180
            if index == 1:
                submit = window.trade.submit_button
                submit_bottom = submit.mapTo(window, submit.rect().bottomLeft()).y()
                assert submit.isVisible() and submit_bottom < window.height() - 20, "紧凑交易页的下单按钮必须直接可见"
            window.grab().save(str(output / f"compact-{index}.png"))
        widget = CoinPilotWidget(config, SettingsStore(path / "settings.json"), start_requests=False)
        widget.show()
        widget.set_unread_events(1)
        app.processEvents()
        widget.grab().save(str(output / "mini.png"))
        assert widget.logical_height == 28
        (output / "report.json").write_text(json.dumps({"scale": scale, "mini_height": widget.logical_height,
            "compact_size": [window.width(), window.height()], "chart_width": window.trade.chart.canvas.width()}, indent=2), encoding="utf-8")
        window.close()
        assert not service.closed
        from unittest.mock import patch
        from coinpilot_ai.cockpit.desktop import DesktopController
        from PyQt6.QtCore import QPoint
        with patch("coinpilot_ai.cockpit.desktop.CockpitService", return_value=service):
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
    print("工作台四页与 28px 迷你窗口已离线渲染：" + str(output))


if __name__ == "__main__":
    main()
