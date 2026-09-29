"""离线图表性能验收：固定数据、临时数据库、无网络或真实账户。"""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def samples_summary(samples):
    ordered = sorted(samples)
    return {"samples": len(samples), "median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[min(len(ordered)-1, math.ceil(len(ordered)*.95)-1)], 3),
            "max_ms": round(ordered[-1], 3)}


def measure(callback, count=80):
    callback()
    samples = []
    for i in range(count):
        start = time.perf_counter()
        callback()
        samples.append((time.perf_counter()-start)*1000)
    return samples_summary(samples)


def candles(count, begin=1700000000000):
    result = []
    for i in range(count):
        base = 100+i*.01+math.sin(i/12)*.5
        result.append([str(begin+i*900000), str(base), str(base+2), str(base-2),
                       str(base+math.cos(i)*.6), str(10+i%17), "0", "0", "0"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true", help="显示自动关闭的 Windows 原生测试窗口")
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts"/"chart-performance")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "windows" if args.native else "offscreen"
    from PyQt6.QtCore import QEventLoop, QPointF, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtGui import QImage
    from coinpilot_ai.application.bootstrap import create_application
    from coinpilot_ai.core.config import DEFAULT_CONFIG
    from coinpilot_ai.charts.canvas import CandleChart
    from coinpilot_ai.charts.state import drawing
    from coinpilot_ai.application.service import CockpitService

    class EmptyVault:
        def read(self, _key):
            return None

    class MeasuredChart(CandleChart):
        def __init__(self, *args):
            self.paint_samples = []
            super().__init__(*args)

        def paintEvent(self, event):
            start = time.perf_counter()
            super().paintEvent(event)
            self.paint_samples.append((time.perf_counter()-start)*1000)

    app = create_application([])
    app.setQuitOnLastWindowClosed(False)
    report = {"platform": os.environ["QT_QPA_PLATFORM"], "size": [950, 530], "history_rows": 50000,
              "drawing_objects": 100, "ema_periods": [20, 60], "synthetic_data": True, "tail_updates": {}}
    with tempfile.TemporaryDirectory(prefix="coinpilot-chart-bench-") as folder:
        path = Path(folder)
        service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), path/"data.db",
                                 path/"cache", vault=EmptyVault(), autostart=False)
        feed, pair = service.chart_feed, (service.selected, service.bar)

        def drain(series):
            deadline = time.monotonic()+10
            while series.job is not None and time.monotonic() < deadline:
                app.processEvents()
                QTest.qWait(1)
            assert series.job is None, "后台任务没有完成"

        window_limit = feed.MEMORY_CANDLE_LIMIT
        # 独立测量 5 万根的计算/绘制扩展性；下方另测生产窗口限制。
        feed.MEMORY_CANDLE_LIMIT = 60000
        for count in (5000, 20000, 50000):
            service.candles[pair] = candles(count)
            series = feed.get_series(pair)
            drain(series)
            feed.raw_index(pair)

            def tick():
                row = list(service.candles[pair][-1])
                row[4] = str(float(row[4])+.0001)
                feed.receive(pair, [row])

            before = series.converted_rows
            report["tail_updates"][str(count)] = measure(tick)
            assert series.converted_rows-before == 81, "尾部更新不应重新转换历史"

        chart = MeasuredChart(service)
        chart.setWindowTitle("离线 K 线性能验证（自动关闭）")
        chart.resize(950, 530)
        chart.set_context(service.environment, *pair)
        chart.bind_series(series)
        chart.show()
        app.processEvents()
        report["device_pixel_ratio"] = chart.devicePixelRatioF()
        image = QImage(chart.size(), QImage.Format.Format_ARGB32_Premultiplied)
        report["render"] = {}
        for count in (100, 500, 2000):
            chart.count = count
            chart.left_time = chart.times[-1]-(count-5)*chart.interval
            chart.objects = [drawing("trend", [[chart.times[-95+i%50], chart.values[-95+i%50][2]],
                                              [chart.times[-20+i%15], chart.values[-20+i%15][2]]])
                             for i in range(100)]

            def rebuild():
                chart._market_key = chart._objects_key = None
                chart.render(image)

            full = measure(rebuild, 40)
            builds = chart.market_builds, chart.object_builds

            def hover():
                chart.pointer = QPointF(200+(len(chart.paint_samples)%50)*5, 180)
                chart.render(image)

            hover_time = measure(hover)
            assert (chart.market_builds, chart.object_builds) == builds, "悬停应复用缓存"
            report["render"][str(count)] = {"full_rebuild": full, "hover": hover_time}

        # 使用与生产图表相同的 33 ms 数据通知合并，16 ms 交互绘制。
        refresh = QTimer(chart)
        refresh.setSingleShot(True)
        refresh.setInterval(33)
        pending = []

        def data_changed(change):
            pending.append(change)
            if not refresh.isActive():
                refresh.start()

        def flush():
            chart.bind_series(series, pending[-1] if len(pending) == 1 else None)
            pending.clear()

        feed.series_changed.connect(data_changed)
        refresh.timeout.connect(flush)
        moves = QTimer(chart)
        moves.setInterval(5)
        moves.timeout.connect(lambda: (tick(), setattr(chart, "pointer", QPointF(300, 200)), chart.request_frame()))
        gaps, previous = [], [time.perf_counter()]
        heartbeat = QTimer(chart)
        heartbeat.setInterval(1)

        def pulse():
            now = time.perf_counter()
            gaps.append((now-previous[0])*1000)
            previous[0] = now

        heartbeat.timeout.connect(pulse)
        chart.paint_samples.clear()
        series.worker_samples.clear()
        history_rows = candles(300, int(service.candles[pair][0][0])-300*900000)
        heartbeat.start()
        moves.start()
        start = time.perf_counter()
        feed.merge(pair, history_rows)
        history_merge_ms = (time.perf_counter()-start)*1000
        # 使用真实 Qt 事件循环，同时测量后台转换期间的响应间隔。
        loop = QEventLoop()
        QTimer.singleShot(1800, loop.quit)
        loop.exec()
        moves.stop()
        heartbeat.stop()
        refresh.stop()
        drain(series)
        flush()
        chart.repaint()
        report["history_merge_ms"] = round(history_merge_ms, 3)
        report["history_workers"] = samples_summary(series.worker_samples)
        report["mixed_paints"] = samples_summary(chart.paint_samples)
        report["event_loop_intervals"] = samples_summary(gaps)
        report["targets_met"] = {
            "hover_p95_le_8ms": report["render"]["2000"]["hover"]["p95_ms"] <= 8,
            "tail_50000_p95_le_5ms": report["tail_updates"]["50000"]["p95_ms"] <= 5,
            "full_2000_p95_le_33ms": report["render"]["2000"]["full_rebuild"]["p95_ms"] <= 33,
            "ui_heartbeat_p95_le_33ms": report["event_loop_intervals"]["p95_ms"] <= 33,
        }
        feed.series_changed.disconnect(data_changed)
        feed.MEMORY_CANDLE_LIMIT = window_limit
        window_pair = ("WINDOW-USDT-SWAP", "15m")
        feed.merge(window_pair, candles(window_limit))
        window_series = feed.get_series(window_pair)
        drain(window_series)
        specs = [{"kind": kind} for kind in ("EMA", "RSI", "MACD", "ATR", "SUPERTREND", "VWAP", "MA", "BOLL")]
        for spec in specs:
            feed.indicator(window_series, spec)
        deadline = time.monotonic()+15
        while feed.indicator_pending and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(1)
        assert not feed.indicator_pending
        converted = window_series.converted_rows
        maximum = [len(window_series.data.rows)]

        def append_window():
            stamp = int(service.candles[window_pair][-1][0])+900000
            feed.receive(window_pair, candles(1, stamp))
            assert window_series.job is None, "实时窗口滚动不应重建历史"
            for spec in specs:
                feed.indicator(window_series, spec)
            maximum[0] = max(maximum[0], len(service.candles[window_pair]))

        report["bounded_window"] = measure(append_window, 1100)
        report["bounded_window"].update({"maximum_rows": maximum[0], "limit": window_limit,
                                         "converted_rows": window_series.converted_rows-converted,
                                         "indicators": len(specs)})
        report["targets_met"].update({
            "window_memory_bounded": maximum[0] <= window_limit,
            "window_append_converts_one_row": window_series.converted_rows-converted == 1101,
            "window_with_indicators_p95_le_5ms": report["bounded_window"]["p95_ms"] <= 5,
        })
        chart.objects = []
        chart.auto_scale = chart.follow = False
        chart.count = chart.plot_rects()[0].width()/8
        chart.left_time = chart.times[-300]
        chart.render(image)
        def scroll():
            chart.left_time += chart.interval
            chart.render(image)
        report["integer_pixel_scroll"] = measure(scroll, 80)
        report["integer_pixel_scroll"]["cache_scrolls"] = getattr(chart, "market_scrolls", 0)
        report["numeric_bytes_per_candle"] = service.candles[window_pair].nbytes/len(service.candles[window_pair])
        report["targets_met"].update({
            "numeric_bytes_per_candle_le_65": report["numeric_bytes_per_candle"] <= 65,
            "scroll_cache_used": report["integer_pixel_scroll"]["cache_scrolls"] >= 80,
            "scroll_p95_le_16ms": report["integer_pixel_scroll"]["p95_ms"] <= 16,
        })
        args.output.mkdir(parents=True, exist_ok=True)
        chart.grab().save(str(args.output/"chart.png"))
        (args.output/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        chart.close()
        service.close()
        chart.deleteLater()
        service.deleteLater()
        app.processEvents()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(report["targets_met"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
