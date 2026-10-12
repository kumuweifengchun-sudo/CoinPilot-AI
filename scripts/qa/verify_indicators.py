"""指标界面离线验收：临时数据库、空凭据、模拟 K 线，生成截图与脚本增量报告。"""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from coinpilot_ai.application.bootstrap import create_application
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.charts.settings import IndicatorEditDialog
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.indicators.conditions import spec_record
from coinpilot_ai.indicators.editor import EXAMPLE, IndicatorEditor
from coinpilot_ai.indicators.models import IndicatorInstance
from coinpilot_ai.indicators.runtime.engine import IndicatorRuntime
from coinpilot_ai.indicators.validation import instance_record
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.intervals import BARS, floor_time, shift
from coinpilot_ai.trading.dialogs import IndicatorConditionDialog
from coinpilot_ai.workbench.ai_cells import AiIndicatorDraftCard
from coinpilot_ai.workbench.window import Workbench

OSCILLATOR = '''//@version=6
indicator("Momentum Pane")
[macd, signal, histogram] = ta.macd(close, 12, 26, 9)
plot(macd, color=color.blue)
plot(signal, color=color.orange)
plot(histogram, color=color.green, style=plot.style_histogram)
hline(0, color=color.gray)
'''


class EmptyVault:
    def read(self, key: str) -> None:
        return None


def wait(app: QApplication, ready: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 15
    while not ready() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert ready(), "后台任务未完成"
    QTest.qWait(40)


def main() -> None:
    app = create_application([])
    if sys.platform == "win32":
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"]) / "Fonts" / name))
    output = ROOT / "artifacts" / "indicators"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coinpilot-indicators-qa-") as directory:
        path = Path(directory)
        service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), path / "business.sqlite3", path / "icons",
                                 vault=EmptyVault(), autostart=False)
        service.refresh_account = lambda: None
        service.refresh_market = lambda: None
        service.fetch_candles = lambda inst, bar: None
        def no_network(*args: object, **kwargs: object) -> None:
            raise AssertionError("离线验收不得请求网络")
        service.api.get = no_network
        instrument, bar = service.selected, service.bar
        end = floor_time(int(time.time()*1000), bar)
        rows = CandleBuffer((shift(end, bar, i-299), 100.+math.sin(i/8)*5, 108.+i%9, 92.-i%5,
            100.+math.sin(i/8)*5, 10.+i%7, 0., 0., int(i < 299)) for i in range(300))
        service.candles[(instrument, bar)] = rows
        overlay = service.indicator_library.save(EXAMPLE, confirmed=True)
        pane = service.indicator_library.save(OSCILLATOR, confirmed=True)
        overlay_instance = IndicatorInstance("overlay", overlay.definition.id, 1)
        pane_instance = IndicatorInstance("pane", pane.definition.id, 1)
        service.chart_book.save_indicators([instance_record(overlay_instance), instance_record(pane_instance),
            {"id": "rsi", "kind": "RSI"}, {"id": "atr", "kind": "ATR"},
            instance_record(IndicatorInstance("dmi", "builtin.dmi", 1))])
        window = Workbench(service)
        window.resize(1440, 960)
        window.show()
        chart = window.ai_context.panel()
        chart.set_data(rows)
        editors: list[IndicatorEditor] = []
        try:
            wait(app, lambda: not service.chart_feed.indicator_pending)
            chart.canvas.calculate_indicators()
            chart.rebuild_indicator_strips()
            QTest.qWait(60)
            assert len(chart.indicator_strips) == 4
            canvas_origin = chart.canvas.mapToGlobal(QPoint(0, 0)).x()
            main, _ = chart.canvas.plot_rects()
            for strip in chart.indicator_strips:
                bounds = strip.plot_bounds()
                assert abs(strip.mapToGlobal(QPoint(0, 0)).x()+bounds.left()-canvas_origin-main.left()) < 1
                assert bounds.width() == main.width()
            assert chart.grab().save(str(output / "chart-panes.png"))
            atr_strip = next(strip for strip in chart.indicator_strips if strip.indicator_id == "atr")
            atr_result = next(result for setting, result in chart.canvas.indicator_results if setting["id"] == "atr")
            atr_plots = service.indicator_catalog.legacy("ATR").plots
            chart.indicator_scroll.ensureWidgetVisible(atr_strip)
            app.processEvents()
            hovered = 260
            QTest.mouseMove(chart.canvas, QPoint(round(chart.canvas.time_x(chart.canvas.times[hovered])), round(main.center().y())))
            app.processEvents()
            expected = atr_result.outputs["value"][hovered]
            assert expected is not None
            assert atr_strip.legend_values(atr_result, atr_plots) == [f"{expected:,.6g}"]
            assert chart.grab().save(str(output / "atr-crosshair.png"))
            dmi_strip = next(strip for strip in chart.indicator_strips if strip.indicator_id == "dmi")
            chart.indicator_scroll.ensureWidgetVisible(dmi_strip)
            app.processEvents()
            QTest.mouseMove(chart.canvas, QPoint(round(chart.canvas.time_x(chart.canvas.times[hovered])), round(main.center().y())))
            dmi_result = next(result for setting, result in chart.canvas.indicator_results if setting["id"] == "dmi")
            assert all(value != "—" for value in dmi_strip.legend_values(dmi_result, service.indicator_catalog.legacy("DMI").plots))
            assert chart.grab().save(str(output / "dmi-crosshair.png"))
            assert dmi_strip.grab().save(str(output / "dmi-pane.png"))
            dmi_settings = IndicatorEditDialog(instance_record(IndicatorInstance("dmi", "builtin.dmi", 1)))
            try:
                dmi_settings.show()
                app.processEvents()
                assert dmi_settings.grab().save(str(output / "dmi-settings.png"))
            finally:
                dmi_settings.reject()
                dmi_settings.deleteLater()
            colors = IndicatorEditDialog({"id": "colors", "kind": "MACD", "color": "#9394ee"})
            try:
                colors.show()
                app.processEvents()
                for button in [colors.color, *(shade for _, shade, _ in colors.plot_fields.values())]:
                    swatch = button.icon().pixmap(button.iconSize()).toImage()
                    assert not swatch.isNull()
                    assert swatch.pixelColor(swatch.width() // 2, swatch.height() // 2).name() == button.text()
                assert colors.grab().save(str(output / "indicator-colors.png"))
                colors.color.set_color("#ffffff")
                swatch = colors.color.icon().pixmap(colors.color.iconSize()).toImage()
                assert swatch.pixelColor(swatch.width() // 2, swatch.height() // 2).name() == "#ffffff"
                assert colors.grab().save(str(output / "indicator-colors-changed.png"))
            finally:
                colors.reject()
                colors.deleteLater()
            def apply(instance: IndicatorInstance) -> None:
                service.chart_book.save_indicators([*service.chart_book.indicators, instance_record(instance)])
            editor = IndicatorEditor(service, apply, source=EXAMPLE, preview_pair=(instrument, bar))
            editors.append(editor)
            editor.show()
            editor.compile_preview()
            wait(app, lambda: editor.job is None)
            assert editor.last_preview is not None and editor.save_button.isEnabled()
            assert editor.grab().save(str(output / "pine-preview.png"))
            definitions = len(service.store.list("indicator_definition"))
            editor.add_to_chart()
            assert len(service.store.list("indicator_definition")) == definitions + 1
            assert len(service.chart_book.indicators) == 6
            editor.code.setPlainText('//@version=6\nindicator("Unsafe")\nplot(request.security("BTC", "1D", close))')
            editor.compile_preview()
            wait(app, lambda: editor.job is None)
            assert not editor.add_button.isEnabled() and "UNSUPPORTED_CALL" in editor.feedback.text()
            assert editor.grab().save(str(output / "pine-diagnostic.png"))
            editor.reject()
            condition = {"metric": "indicator", "indicator": spec_record(overlay_instance), "line": "buy", "op": "is_true", "bar": bar}
            dialog = IndicatorConditionDialog(condition, catalog=service.indicator_catalog)
            dialog.show()
            QTest.qWait(40)
            assert not dialog.threshold.isEnabled()
            assert dialog.grab().save(str(output / "boolean-condition.png"))
            dialog.reject()
            dialog.deleteLater()
            chat = service.ai_chat
            assert chat is not None
            turn = chat.store.start_turn(chat.conversation.id, "生成 EMA 买点指标", scope=chat.scope)
            chat.store.append_item(turn.id, scope=chat.scope, kind="indicator_draft", payload={
                "source": EXAMPLE, "valid": True, "requires_confirmation": True, "description": "EMA 金叉买点；预览后由用户添加。"})
            chat.store.finish_turn(turn.id, scope=chat.scope)
            window.ai_chat.reload()
            QTest.qWait(60)
            draft = next(cell for cell in window.ai_chat._cells.values() if isinstance(cell, AiIndicatorDraftCard))
            assert draft.grab().save(str(output / "ai-indicator-draft.png"))
            # 5 万根脚本历史只预热一次，尾部修正须只处理一根，引用同一个 CandleBuffer。
            history = CandleBuffer((shift(end, bar, i-49999), 100., 110., 90.,
                                    100.+math.sin(i/8)*5, 10., 0., 0., 1) for i in range(50000))
            runtime = IndicatorRuntime(overlay_instance, bar=bar, catalog=service.indicator_catalog)
            started = time.perf_counter()
            runtime.update(history, 0)
            full_ms = (time.perf_counter()-started)*1000
            samples: list[float] = []
            count = runtime.processed_rows
            buffers = runtime.times, dict(runtime.result.outputs)
            for i in range(80):
                row = list(history[-1]); row[4] = 100.+i*.01
                history[-1] = row
                started = time.perf_counter()
                runtime.update(history, len(history)-1)
                samples.append((time.perf_counter()-started)*1000)
            assert runtime.processed_rows-count == 80
            assert runtime.times is buffers[0]
            assert all(runtime.result.outputs[key] is value for key, value in buffers[1].items())
            report = {"history_rows": 50000, "full_ms": round(full_ms, 2),
                      "tail_updates": 80, "processed_tail_rows": runtime.processed_rows-count,
                      "tail_p95_ms": round(sorted(samples)[75], 3), "tail_max_ms": round(max(samples), 3),
                      "pane_time_axes_aligned": True, "no_network": True, "bar_seconds": BARS[bar]}
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
            assert report["tail_p95_ms"] <= 5
        finally:
            for editor in editors:
                editor.reject()
                editor.deleteLater()
            window.exiting = True
            window.close()
            service.close()
            window.deleteLater()
            service.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
