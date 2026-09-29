"""交易所日历周期：闰月、季度、时区、聚合与真实缺口。"""
from datetime import datetime

import pytest

from coinpilot_ai.market.intervals import BARS, floor_time, shift, openings, contiguous
from coinpilot_ai.market.cache import MarketCache
from coinpilot_ai.market.history import HistoryData
from coinpilot_ai.market.indicators import calculate, IndicatorStream
from coinpilot_ai.market.lod import display_bar
from coinpilot_ai.research.backtest import run_backtest


def ts(value):
    return int(datetime.fromisoformat(value).timestamp()*1000)


def rows(bar, begin, count):
    return [(shift(begin, bar, i), 100.+i, 103.+i, 99.+i, 101.+i, 10., 20., 30., 1)
            for i in range(count)]


@pytest.mark.parametrize("bar", BARS)
def test_exchange_openings_round_trip(bar):
    begin = floor_time(ts("2024-02-29T17:22:31+00:00"), bar)
    end = shift(begin, bar, 3)
    assert list(openings(begin, end, bar)) == [shift(begin, bar, i) for i in range(4)]
    assert floor_time(shift(begin, bar)-1, bar) == begin
    assert shift(end, bar, -3) == begin
    assert contiguous(begin, shift(begin, bar), bar)
    assert not contiguous(begin, shift(begin, bar, 2), bar)


@pytest.mark.parametrize("bar,date,expected", [
    ("1W", "2026-09-29T05:00:00+00:00", "2026-09-27T16:00:00+00:00"),
    ("1Wutc", "2026-09-29T05:00:00+00:00", "2026-09-28T00:00:00+00:00"),
    ("2D", "2026-09-29T05:00:00+00:00", "2026-09-27T16:00:00+00:00"),
    ("3D", "2026-09-29T05:00:00+00:00", "2026-09-27T16:00:00+00:00"),
    ("5D", "2026-09-29T05:00:00+00:00", "2026-09-28T16:00:00+00:00"),
    ("1M", "2024-02-29T15:59:59+00:00", "2024-01-31T16:00:00+00:00"),
    ("1M", "2024-02-29T16:00:00+00:00", "2024-02-29T16:00:00+00:00"),
    ("3Mutc", "2024-02-29T16:00:00+00:00", "2024-01-01T00:00:00+00:00"),
])
def test_exchange_boundaries(bar, date, expected):
    assert floor_time(ts(date), bar) == ts(expected)


@pytest.mark.parametrize("bar", ["1M", "1Mutc", "3M", "3Mutc", "1W", "5Dutc"])
def test_calendar_cache_gaps_and_dataset(tmp_path, bar):
    cache = MarketCache(tmp_path/"calendar.db")
    begin = floor_time(ts("2023-12-15T00:00:00+00:00"), bar)
    data = rows(bar, begin, 6)
    try:
        cache.put("BTC", bar, data[:2]+data[3:])
        assert list(cache.gap_ranges("BTC", bar, begin, data[-1][0])) == [(data[2][0], data[2][0])]
        with pytest.raises(ValueError, match="缺口"):
            cache.dataset("BTC", bar, begin, data[-1][0])
        cache.put("BTC", bar, data[2:3])
        assert cache.has_range("BTC", bar, begin, data[-1][0])
        assert cache.coverage("BTC", bar, data[1][0]+1, data[-1][0]-1)["segments"] == [
            {"begin": data[2][0], "end": data[-2][0], "source": "okx"}]
        snapshot = cache.dataset("BTC", bar, begin, data[-1][0])
        try:
            assert len(snapshot) == 6
            assert list(snapshot.window(2, begin, 100, bar, bar)) == data[:3]
        finally:
            snapshot.close()
    finally:
        cache.close()


@pytest.mark.parametrize("suffix", ["", "utc"])
def test_leap_february_and_quarter_aggregation(tmp_path, suffix):
    cache = MarketCache(tmp_path/"aggregate.db")
    day, month, quarter = "1D"+suffix, "1M"+suffix, "3M"+suffix
    begin = floor_time(ts("2024-02-10T00:00:00+00:00"), month)
    data = rows(day, begin, 29)
    try:
        cache.put("BTC", day, data[:-1])
        assert not cache.aggregate_range("BTC", day, month, begin, begin)
        cache.put("BTC", day, data[-1:])
        result = cache.aggregate_range("BTC", day, month, begin, begin)
        assert len(result) == 1 and result[0][5] == 290
        qbegin = floor_time(begin, quarter)
        cache.put("BTC", month, rows(month, qbegin, 3))
        assert cache.aggregate_range("BTC", month, quarter, qbegin, qbegin)[0][5] == 30
        snapshot = HistoryData.create(data, BARS[day]*1000, bar=day)
        try:
            assert snapshot.window(27, begin, 30, day, month)[0][8] == 0
            assert snapshot.window(28, begin, 30, day, month)[0][8] == 1
        finally:
            snapshot.close()
    finally:
        cache.close()


def test_monthly_indicators_keep_warmup_and_reset_only_on_real_gap():
    data = rows("1Mutc", ts("2023-12-01T00:00:00+00:00"), 6)
    spec = {"kind": "MA", "params": {"period": 3}}
    expected = calculate(data, spec)
    result = calculate(data, spec, bar="1Mutc")
    assert result.lines == expected.lines
    stream = IndicatorStream(spec, "1Mutc")
    for row in data:
        stream.push(row)
    assert list(stream.state.result.lines["value"]) == list(expected.lines["value"])
    gap = calculate(data[:2]+data[3:], spec, bar="1Mutc")
    assert gap.lines["value"][2:4] == (None, None)


def test_monthly_backtest_accepts_leap_year_and_rejects_missing_month():
    data = rows("1M", ts("2023-12-31T16:00:00+00:00"), 6)
    strategy = {"bar": "1M", "direction": "long", "stop_percent": "1", "target_percent": "1",
                "risk_percent": "1", "conditions": [{"metric": "indicator", "indicator": {"kind": "MA", "params": {"period": 2}},
                "op": "above", "threshold": "999"}]}
    spec = {"ctType": "linear", "settleCcy": "USDT", "ctVal": "1", "lotSz": "1", "minSz": "1"}
    assert run_backtest(data, strategy, spec)["coverage"]["bars"] == 6
    with pytest.raises(ValueError, match="缺口"):
        run_backtest(data[:2]+data[3:], strategy, spec)


def test_lod_preserves_timezone_and_calendar_compatible_aggregation():
    assert display_bar("1Dutc", 10000, 900) in ("1Wutc", "1Mutc", "3Mutc")
    assert display_bar("1M", 100000, 900) == "3M"
    assert display_bar("1W", 100000, 900) == "1W"  # 周线不能拆成自然月。
