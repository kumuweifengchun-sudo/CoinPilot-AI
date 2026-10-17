from collections.abc import Iterator
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import time
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.charts.snapshot import CapturedChart, capture_chart
from coinpilot_ai.charts.state import drawing
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.core.store import Store
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.intervals import BARS, floor_time, shift
from coinpilot_ai.indicators.models import IndicatorInstance
from coinpilot_ai.indicators.validation import instance_record
from coinpilot_ai.review.manual import ManualFill, ManualLedger, ManualTrade, journal_row
from coinpilot_ai.review.manual_widgets import ManualEntry
from coinpilot_ai.review.snapshot_widgets import SnapshotGallery
from coinpilot_ai.review.snapshots import MAX_SNAPSHOTS, ChartSnapshot
from coinpilot_ai.workbench.window import Workbench


class EmptyVault:
    def read(self, key: str) -> None:
        return None


def candles(period: str) -> CandleBuffer:
    end = floor_time(int(time.time() * 1000), period)
    return CandleBuffer((shift(end, period, i - 299), 100 + math.sin(i / 8) * 5,
                         108 + i % 5, 92 - i % 5, 100 + math.sin(i / 8) * 5, 10 + i % 7, 0, 0, 1)
                        for i in range(300))


def sample(book: str) -> ManualTrade:
    return ManualTrade(book_id=book, fees_known=True, funding_known=True,
                       fills=[ManualFill(time=int(time.time() * 1000) - 10000, price="100", amount="1000")])


@pytest.fixture
def workbench(tmp_path: Path, app: QApplication) -> Iterator[tuple[Workbench, CockpitService]]:
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path / "snapshot.db", tmp_path / "icons",
                             vault=EmptyVault(), autostart=False)
    service.refresh_account = lambda: None
    service.refresh_market = lambda: None
    service.fetch_candles = lambda inst, bar: None
    with patch.object(service.api, "get", side_effect=AssertionError("快照不得联网")), \
         patch.object(service.api, "post", side_effect=AssertionError("快照不得下单")):
        service.chart_book.save_indicators([{"id": "rsi", "kind": "RSI"}, {"id": "ma", "kind": "MA"}])
        window = Workbench(service)
        window.resize(1440, 960)
        window.multi_chart.layout_choice.setCurrentIndex(1)
        window.show()
        for panel in window.multi_chart.panels[:2]:
            service.candles[panel.pair] = candles(panel.bar)
            panel.refresh("candles")
        QTest.qWait(120)
        for panel in window.multi_chart.panels[:2]:
            panel.set_data(service.candles[panel.pair])
            panel.canvas.objects = [drawing("trend", [[panel.canvas.times[-75], 96], [panel.canvas.times[-10], 108]], "入场依据")]
            panel.canvas.calculate_indicators()
            panel.rebuild_indicator_strips()
        QTest.qWait(80)
        try:
            yield window, service
        finally:
            window.exiting = True
            window.close()
            service.close()
            window.deleteLater()
            app.processEvents()


def test_real_multiperiod_capture_contains_drawings_indicators_and_does_not_mutate(workbench: tuple[Workbench, CockpitService]) -> None:
    window, service = workbench
    panels = window.multi_chart.panels[:2]
    before = [(p.bar, p.canvas.count, p.canvas.left_time, json.dumps(p.canvas.objects)) for p in panels]
    captured = window.capture_trade_charts(True)
    assert len(captured) == 2 and {c.period for c in captured} == {"15m", "1H"}
    assert all(not c.image.isNull() and c.image.width() > 260 and c.image.height() > 180 for c in captured)
    state = json.loads(captured[0].state_json)
    assert state["drawings"][0]["text"] == "入场依据"
    assert {i["kind"] for i in state["indicators"]} == {"RSI", "MA"}
    assert state["viewport"]["visible_begin"] is not None
    assert before == [(p.bar, p.canvas.count, p.canvas.left_time, json.dumps(p.canvas.objects)) for p in panels]
    panels[0].canvas.objects[0]["text"] = "后续修改"
    panels[0].indicator_book.indicators[0]["params"]["period"] = 99
    assert json.loads(captured[0].state_json)["drawings"][0]["text"] == "入场依据"
    window.ai_context.active_panel = panels[1]
    assert window.capture_trade_charts(False)[0].period == "1H"
    panels[0].canvas.preview = drawing("horizontal", [[panels[0].canvas.times[-1], 100]])
    with pytest.raises(ValueError, match="正在绘制"):
        capture_chart(panels[0])
    panels[0].canvas.preview = None
    panels[0].pending_kinds.add("selection")
    with pytest.raises(ValueError, match="加载完成"):
        capture_chart(panels[0])


def test_capture_save_restart_remove_and_gallery_integrity(workbench: tuple[Workbench, CockpitService], tmp_path: Path) -> None:
    window, service = workbench
    book = service.manual_ledger.create_book("交易快照", "示例交易所", "A")
    entry = window.trade.manual_entry
    entry.load_trade(sample(book))
    window.trade.entry_mode.setCurrentIndex(1)
    entry.snapshot_note.setText("多周期确认")
    entry.capture_snapshots(True)
    assert len(entry.snapshots) == 2, entry.feedback.text()
    draft_files = [Path(s.image_path) for s in entry.snapshots]
    entry.save()
    assert "已保存" in entry.feedback.text()
    assert all(not p.exists() for p in draft_files)
    trade_id = entry.trade.id
    snapshots = service.manual_ledger.get(book, trade_id).chart_snapshots
    assert len(snapshots) == 2 and all(Path(s.image_path).is_file() for s in snapshots)
    assert all("charts" in Path(s.image_path).parts for s in snapshots)
    gallery = SnapshotGallery(snapshots, window)
    gallery.show()
    QTest.qWait(20)
    assert not gallery.picture.pixmap().isNull()
    gallery.items.setCurrentRow(1)
    assert "1H" in gallery.description.text()
    gallery.close()
    entry.snapshot_list.setCurrentRow(0)
    entry.remove_snapshot()
    entry.save()
    assert len(service.manual_ledger.get(book, trade_id).chart_snapshots) == 1
    prior = service.store.get("manual_revision", trade_id + ":1", scope=service.manual_ledger.scope(book))
    assert len(prior["chart_snapshots"]) == 2
    assert Path(snapshots[0].image_path).exists()  # 移除引用不能删除旧版本证据。
    reopened = Store(service.store.path)
    try:
        loaded = ManualLedger(reopened).get(book, trade_id)
        assert loaded.chart_snapshots[0].state_json == snapshots[1].state_json
        row = journal_row(loaded)
        serialized = json.dumps(row)
        assert "state_json" not in serialized and "image_path" not in serialized
        changed = replace(loaded.chart_snapshots[0], note="篡改采集说明")
        loaded.chart_snapshots = [changed]
        with pytest.raises(ValueError, match="不能修改"):
            ManualLedger(reopened).save(loaded)
    finally:
        reopened.close()
    Path(snapshots[1].image_path).write_bytes(b"corrupted")
    broken = SnapshotGallery([snapshots[1]], window)
    assert "校验失败" in broken.picture.text()
    broken.close()


def test_legacy_record_and_invalid_snapshot_inputs(tmp_path: Path) -> None:
    original = sample("")
    raw = asdict(original)
    raw.pop("chart_snapshots")
    assert ManualTrade.parse(raw).chart_snapshots == []
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.green)
    path = tmp_path / "s.png"
    assert image.save(str(path), "PNG")
    import uuid
    snapshot = ChartSnapshot(uuid.uuid4().hex, str(path), hashlib.sha256(path.read_bytes()).hexdigest(),
        int(time.time() * 1000), "BTC-USDT-SWAP", "15m", "15m", "分析", "", '{"version":1}')
    assert ChartSnapshot.parse(asdict(snapshot)) == snapshot
    with pytest.raises(ValueError, match="周期"):
        ChartSnapshot.parse(asdict(replace(snapshot, period="invalid")))
    store = Store(tmp_path / "limits.db")
    ledger = ManualLedger(store)
    book = ledger.create_book("上限", "测试", "A")
    trade = sample(book)
    trade.chart_snapshots = [replace(snapshot, id=uuid.uuid4().hex) for _ in range(MAX_SNAPSHOTS + 1)]
    with pytest.raises(ValueError, match="最多"):
        ledger.save(trade)
    trade.chart_snapshots = [snapshot]
    path.write_bytes(b"bad")
    with pytest.raises(ValueError, match="校验失败"):
        ledger.save(trade)
    assert ledger.trades(book) == []
    store.close()


def test_custom_indicator_source_version_is_frozen(workbench: tuple[Workbench, CockpitService]) -> None:
    window, service = workbench
    source = '//@version=6\nindicator("Snapshot SMA")\nplot(ta.sma(close, 7))\n'
    program = service.indicator_library.save(source, confirmed=True)
    instance = IndicatorInstance("snapshot-script", program.definition.id, program.definition.revision)
    service.chart_book.save_indicators([instance_record(instance)])
    QTest.qWait(80)
    panel = window.multi_chart.panels[0]
    panel.canvas.calculate_indicators()
    frozen = capture_chart(panel)
    state = json.loads(frozen.state_json)
    assert state["indicator_definitions"][0]["source"] == source
    assert state["indicator_definitions"][0]["definition"]["revision"] == 1
    service.indicator_library.save(source.replace(", 7", ", 21"), identity=program.definition.id, expected_revision=1, confirmed=True)
    assert json.loads(frozen.state_json)["indicator_definitions"][0]["source"] == source
