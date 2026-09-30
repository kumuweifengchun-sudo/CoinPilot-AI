"""列式缓存、后台发布、LOD 与流式指标的独立回归。"""
import math
import time
from threading import Event

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtCore import QTimer
from coinpilot_ai.market.buffer import CandleBuffer, CandleIndex, ValueView, candle
from coinpilot_ai.market.cache import MarketCache
from coinpilot_ai.market.history import HistoryData
from coinpilot_ai.market.jobs import WorkQueue
from coinpilot_ai.market.lod import display_bar, pixel_buckets
from coinpilot_ai.market.indicators import KINDS, IndicatorStream, calculate


def rows(count, step=60000, begin=300000):
    for i in range(count):
        price = 100+math.sin(i/9)*3+i*.001
        yield (begin+i*step, price, price+2, price-2, price+.2, 10+i%7, 20., 30., 1)


def wait_for(predicate, timeout=10):
    deadline = time.monotonic()+timeout
    while not predicate():
        QTest.qWait(1)
        assert time.monotonic() < deadline


def test_numeric_buffer_aliases_and_compact_size():
    data = CandleBuffer(rows(10000))
    assert data.nbytes == 650000
    assert data.times is data.columns[0]
    view, index = ValueView(data), CandleIndex(data)
    replacement = list(data[-1])
    replacement[4] += .1
    data[-1] = replacement
    assert index[data.times[-1]][4] == view[-1][3] == replacement[4]
    assert isinstance(data[-1], tuple)
    del data[:9000]
    assert len(view) == len(index) == 1000
    assert data.nbytes == 65000


def test_history_preserves_sequence_count_and_slice_buffer():
    history = HistoryData.create(rows(3), 60000)
    try:
        first = history[0]
        assert history.count(first) == 1
        assert history.index(history[-1]) == 2
        assert isinstance(history[:2], CandleBuffer)
        assert history[:2] == [first, history[1]]
    finally:
        history.close()


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_stream_correction_gap_and_rollover_equal_reference(kind):
    stream = IndicatorStream({"kind": kind}, "1m", limit=150)
    history = []
    for i, row in enumerate(rows(410)):
        row = list(row)
        if i >= 210:
            row[0] += 60000
        history.append(row)
        stream.push(row)
        if i % 7 == 0:
            history[-1][4] -= .3
            stream.push(history[-1])
        if i in (100, 200, 240, 409):
            expected = calculate(history, {"kind": kind}, bar="1m")
            offset = len(history)-len(stream.rows)
            assert len(stream.rows) <= 150
            for key, values in expected.lines.items():
                assert list(stream.state.result.lines[key]) == pytest.approx(values[offset:], abs=2e-9)


def test_materialized_pyramid_keeps_extremes_and_rejects_incomplete_bucket(tmp_path):
    cache = MarketCache(tmp_path/"candles.db")
    data = list(rows(10))
    data[2] = (*data[2][:2], 900., .1, *data[2][4:])
    try:
        cache.put("BTC", "1m", data[:4]+data[5:])
        coarse = cache.aggregate_range("BTC", "1m", "5m", 300000, 900000)
        assert len(coarse) == 1 and coarse[0][0] == 600000
        cache.put("BTC", "1m", data[4:5])
        coarse = cache.aggregate_range("BTC", "1m", "5m", 300000, 900000)
        assert len(coarse) == 2
        assert coarse[0][1:5] == (data[0][1], 900., .1, data[4][4])
        assert coarse[0][5] == sum(row[5] for row in data[:5])
        changed = list(data[2]); changed[2] = 950
        cache.put("BTC", "1m", [changed])
        assert cache.aggregate_range("BTC", "1m", "5m", 300000, 300000)[0][2] == 950
    finally:
        cache.close()


def test_daily_pyramid_uses_exchange_day_boundary(tmp_path):
    cache = MarketCache(tmp_path/"daily.db")
    begin = 16*3600000
    try:
        cache.put("BTC", "1H", rows(24, 3600000, begin))
        result = cache.aggregate_range("BTC", "1H", "1D", begin, begin)
        assert len(result) == 1 and result[0][0] == begin and result[0][8] == 1
    finally:
        cache.close()


def test_lod_and_pixel_buckets_preserve_extremes_and_gaps():
    data = CandleBuffer(rows(3000))
    altered = list(data[17]); altered[2] = 900.; altered[3] = .1; data[17] = altered
    assert display_bar("1m", 525600, 900) == "12H"
    assert display_bar("1m", 100, 900) == "1m"
    grouped = pixel_buckets(data, data[0][0], 3000*60000, 300, 60000)
    assert len(grouped) <= 301
    assert max(row[2] for row in grouped) == 900
    assert min(row[3] for row in grouped) == .1
    assert sum(row[5] for row in grouped) == sum(data.columns[5])
    assert grouped[0][1] == data[0][1] and grouped[-1][4] == data[-1][4]
    gap = CandleBuffer([data[0], data[2]])
    assert len(pixel_buckets(gap, data[0][0], 3000*60000, 1, 60000)) == 2


def test_history_snapshot_is_paged_and_cleanup_is_explicit():
    data = HistoryData.create(rows(15000), 60000)
    path = data.path
    try:
        for i in range(0, len(data), 511):
            assert data[i][0] == 300000+i*60000
        assert len(data.pages) <= 2
        assert sum(len(page) for page in data.pages.values()) <= 2*1024*65
        assert len(data[14000:]) == 1000
    finally:
        data.close()
    assert not path.exists()


def test_background_work_keeps_ui_alive_and_cancel_disposes_result(app):
    queue = WorkQueue()
    release, began = Event(), Event()
    delivered, pulses = [], []
    timer = QTimer(); timer.setInterval(1); timer.timeout.connect(lambda: pulses.append(1)); timer.start()
    def work():
        began.set()
        release.wait(5)
        return HistoryData.create(rows(100), 60000)
    try:
        token = queue.submit(work, lambda value, error: delivered.append((value, error)))
        wait_for(began.is_set)
        wait_for(lambda: len(pulses) >= 2, timeout=1)
        assert not delivered
        queue.cancel(token)
        future = queue.futures[token]
        release.set()
        wait_for(lambda: not queue.futures)
        assert future.result().closed and not delivered
    finally:
        release.set(); timer.stop(); queue.close()


def test_huge_gap_returns_one_interval(tmp_path):
    cache = MarketCache(tmp_path/"gaps.db")
    try:
        assert list(cache.gap_ranges("BTC", "1m", 60000, 60_000_000_000)) == [(60000, 60_000_000_000)]
    finally:
        cache.close()



def test_replay_window_aggregates_only_known_prefix():
    data = [list(row) for row in rows(100)]
    future = list(data[17]); future[2] = 99999; data[17] = future
    snapshot = HistoryData.create(data, 60000)
    try:
        window = snapshot.window(16, 300000, 100, "1m", "5m")
        assert window[-1][8] == 0
        assert window[-1][0] == data[15][0]
        assert max(row[2] for row in window) < 200
        assert sum(row[5] for row in window) == sum(row[5] for row in data[:17])
        complete = snapshot.window(19, 300000, 100, "1m", "5m")
        assert complete[-1][8] == 1 and complete[-1][2] == 99999
    finally:
        snapshot.close()



def test_unconfirmed_history_restarts_indicator_warmup():
    from coinpilot_ai.market.indicators import IndicatorState
    data = [list(row) for row in rows(10)]
    data[6] = [*data[6][:8], 0]
    state = IndicatorState({"kind": "MA", "params": {"period": 3}}, bar="1m")
    result = state.update(data, 0)
    assert list(result.lines["value"])[7:9] == [None, None]
    expected = calculate(data, {"kind": "MA", "params": {"period": 3}}, bar="1m")
    assert list(result.lines["value"]) == pytest.approx(expected.lines["value"])



def test_cancelled_history_read_removes_partial_snapshot(tmp_path, monkeypatch):
    import coinpilot_ai.market.history as module
    from tempfile import NamedTemporaryFile
    monkeypatch.setattr(module, "NamedTemporaryFile", lambda **kwargs: NamedTemporaryFile(dir=tmp_path, **kwargs))
    count = [0]
    def source():
        for row in rows(10000):
            count[0] += 1
            yield row
    with pytest.raises(ValueError, match="取消"):
        HistoryData.create(source(), 60000, cancelled=lambda: count[0] > 1024)
    assert count[0] < 10000
    assert list(tmp_path.iterdir()) == []
