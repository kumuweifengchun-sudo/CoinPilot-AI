"""使用真实多图组件采集画线和指标快照，离线检查截图及采集耗时。"""
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QScrollArea
from coinpilot_ai.application.bootstrap import create_application
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.charts.state import drawing
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.intervals import floor_time, shift
from coinpilot_ai.review.manual import ManualFill, ManualTrade
from coinpilot_ai.review.snapshot_widgets import SnapshotGallery
from coinpilot_ai.workbench.window import Workbench


class EmptyVault:
    def read(self, key: str) -> None:
        return None


def main() -> None:
    app = create_application([])
    for name in ("msyh.ttc", "msyhbd.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"]) / "Fonts" / name))
    output = ROOT / "artifacts" / "trade-snapshots"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coinpilot-snapshot-qa-") as directory:
        folder = Path(directory)
        service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), folder / "manual.db", folder / "icons",
                                 vault=EmptyVault(), autostart=False)
        service.refresh_market = lambda: None
        service.refresh_account = lambda: None
        service.fetch_candles = lambda inst, bar: None
        with patch.object(service.api, "get", side_effect=AssertionError("禁止网络")), \
             patch.object(service.api, "post", side_effect=AssertionError("禁止下单")):
            service.chart_book.save_indicators([{"id": "rsi", "kind": "RSI"}, {"id": "ma", "kind": "MA"}])
            window = Workbench(service)
            window.resize(1600, 1000)
            window.multi_chart.layout_choice.setCurrentIndex(1)
            window.show()
            try:
                for panel in window.multi_chart.panels[:2]:
                    end = floor_time(int(time.time() * 1000), panel.bar)
                    rows = CandleBuffer((shift(end, panel.bar, i - 299), 100 + math.sin(i / 8) * 5, 108 + i % 5,
                        92 - i % 5, 100 + math.sin(i / 8) * 5, 10 + i % 7, 0, 0, 1) for i in range(300))
                    service.candles[panel.pair] = rows
                    panel.refresh("candles")
                QTest.qWait(120)
                for panel in window.multi_chart.panels[:2]:
                    panel.set_data(service.candles[panel.pair])
                    line = drawing("trend", [[panel.canvas.times[-75], 96], [panel.canvas.times[-10], 108]], "入场依据")
                    line["color"] = "#ffcb47"
                    line["width"] = 3
                    panel.canvas.objects = [line]
                    panel.canvas.calculate_indicators()
                    panel.rebuild_indicator_strips()
                QTest.qWait(80)
                book = service.manual_ledger.create_book("多周期交易复盘", "示例交易所", "主账户")
                entry = window.trade.manual_entry
                entry.load_trade(ManualTrade(book_id=book, fees_known=True, funding_known=True,
                    fills=[ManualFill(time=int(time.time() * 1000) - 10000, price="100", amount="1000")]))
                entry.snapshot_note.setText("趋势线与 RSI / MA 多周期确认")
                started = time.perf_counter()
                entry.capture_snapshots(True)
                elapsed = (time.perf_counter() - started) * 1000
                assert len(entry.snapshots) == 2, entry.feedback.text()
                entry.save()
                assert "已保存" in entry.feedback.text()
                for snapshot in entry.snapshots:
                    shutil.copyfile(snapshot.image_path, output / ("chart-" + snapshot.period + ".png"))
                gallery = SnapshotGallery(entry.snapshots, window)
                gallery.show()
                QTest.qWait(50)
                gallery.show_selected()
                gallery.grab().save(str(output / "gallery.png"))
                gallery.items.setCurrentRow(1)
                assert "1H" in gallery.description.text()
                gallery.close()
                window.trade.entry_mode.setCurrentIndex(1)
                window.workspace.set_visible("order", True)
                dock = window.workspace.docks["order"]
                dock.setFloating(True)
                dock.resize(460, 900)
                dock.show()
                QTest.qWait(50)
                area = entry.findChild(QScrollArea)
                assert area is not None
                area.ensureWidgetVisible(entry.snapshot_list)
                app.processEvents()
                dock.grab().save(str(output / "entry.png"))
                print(f"PASS: 15m + 1H real charts, drawings + RSI/MA, saved gallery; capture {elapsed:.1f} ms; no network")
            finally:
                window.exiting = True
                window.close()
                service.close()
                window.deleteLater()
                app.processEvents()


if __name__ == "__main__":
    main()
