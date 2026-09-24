"""生成真实 Qt 窗口截图并检查常用缩放；所有数据写入独立临时目录。"""

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def capture(output):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtGui import QFontInfo, QFontMetricsF
    from PyQt6.QtWidgets import QLabel
    from coinpilot_ai.app import create_application
    from coinpilot_ai.config import DEFAULT_CONFIG, SettingsStore
    from coinpilot_ai.settings import SettingsDialog
    from coinpilot_ai.widget import CoinPilotWidget
    from coinpilot_ai.visuals import font, price_text_width

    app = create_application([])
    output.mkdir(parents=True, exist_ok=True)
    scale = os.environ.get("COINPILOT_AI_QA_SCALE", "1")
    with tempfile.TemporaryDirectory(prefix="coinpilot-ai-qa-") as directory:
        widget = CoinPilotWidget(DEFAULT_CONFIG, SettingsStore(Path(directory) / "settings.json"), start_requests=False)
        widget.update_timer.stop()
        widget.cycle_timer.stop()
        for symbol, price in (("BTCUSDT", "68432.18"), ("ETHUSDT", "3518.42"), ("SOLUSDT", "148.63")):
            widget._price_ready(symbol, Decimal(price), "")
        widget.show()
        dialog = SettingsDialog(widget)
        dialog.show()
        QTest.qWait(100)
        assert abs(dialog.devicePixelRatioF() - float(scale)) < 0.02, "实际缩放比例不匹配"
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        collapsed_scroll_range = dialog.scroll.verticalScrollBar().maximum()
        assert dialog.proxy_host_edit.width() >= 80, "代理地址输入框应可读"
        assert not app.translator.isEmpty(), "中文 Qt 翻译未加载"
        price_font = font(12, True, latin=True)
        assert app.inter_font_loaded and QFontInfo(price_font).family() == "Inter Variable", "应加载随包的 Inter 4.1"
        price_metrics = QFontMetricsF(price_font, widget)
        digit_widths = [price_text_width(f"{digit}{digit},{digit}{digit}{digit}.{digit}{digit}", price_metrics) for digit in range(10)]
        assert max(digit_widths) - min(digit_widths) < 0.01, "价格数字列宽应一致"
        for edit, spin in zip(dialog.symbol_edits, dialog.decimal_spins):
            assert edit.width() > 100
            assert spin.width() >= 50
        for number in dialog.number_inputs.values():
            assert number.fontMetrics().horizontalAdvance(number.text()) + 16 <= number.width()
        assert dialog.grab().save(str(output / f"settings-{scale}-top.png"))
        dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
        QTest.qWait(50)
        assert dialog.grab().save(str(output / f"settings-{scale}-bottom.png"))
        assert widget.grab().save(str(output / f"widget-{scale}.png"))
        menu = widget._create_context_menu()
        menu.popup(widget.geometry().bottomLeft())
        QTest.qWait(50)
        assert menu.font().pixelSize() == 11
        assert menu.width() < 220 and menu.height() < 200, "带图标的右键菜单应保持紧凑"
        assert menu.grab().save(str(output / f"menu-{scale}.png"))
        source_menu = menu.actions()[1].menu()
        source_menu.popup(menu.geometry().topRight())
        QTest.qWait(50)
        assert source_menu.font().pixelSize() == 11
        assert source_menu.grab().save(str(output / f"source-menu-{scale}.png"))
        source_menu.close()
        menu.close()
        menu.deleteLater()
        widget.set_mini_mode(False)
        assert widget.grab().save(str(output / f"widget-{scale}-full.png"))
        widget.set_mini_mode(True)
        widget._price_ready("BTCUSDT", None, "网络请求失败")
        assert widget.grab().save(str(output / f"widget-{scale}-error.png"))
        dialog.mini_check.setChecked(False)
        dialog.number_inputs["text_size"].setValue(64)
        dialog.number_inputs["bg_opacity"].setValue(100)
        dialog.preview_check.setChecked(True)
        dialog.scroll.ensureWidgetVisible(dialog.preview)
        QTest.qWait(50)
        assert dialog.grab().save(str(output / f"settings-{scale}-large-font.png"))
        report = {"scale": scale, "device_pixel_ratio": dialog.devicePixelRatioF(),
                  "dialog_size": [dialog.width(), dialog.height()], "widget_size": [widget.width(), widget.height()],
                  "horizontal_overflow": dialog.scroll.horizontalScrollBar().maximum(),
                  "vertical_scroll_range": collapsed_scroll_range,
                  "expanded_preview_scroll_range": dialog.scroll.verticalScrollBar().maximum(),
                  "price_font": QFontInfo(price_font).family(),
                  "price_pixel_size": price_font.pixelSize(),
                  "equal_digit_widths": True,
                  "chinese_translation": not app.translator.isEmpty()}
        (output / f"report-{scale}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        dialog.close()
        widget.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description="界面截图与显示缩放验证")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "screenshots")
    args = parser.parse_args()
    if args.all:
        base_env = dict(os.environ)
        for key in ("QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_QPA_PLATFORM"):
            base_env.pop(key, None)
        probe = "from PyQt6.QtGui import QGuiApplication; app=QGuiApplication([]); print(app.primaryScreen().devicePixelRatio())"
        native_scale = float(subprocess.check_output([sys.executable, "-c", probe], env=base_env, text=True).strip())
        for scale in ("1", "1.25", "1.5", "1.75", "2"):
            env = {**base_env, "QT_SCALE_FACTOR": str(float(scale) / native_scale), "COINPILOT_AI_QA_SCALE": scale}
            subprocess.run([sys.executable, __file__, "--output", str(args.output)], env=env, check=True)
        return 0
    return capture(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
