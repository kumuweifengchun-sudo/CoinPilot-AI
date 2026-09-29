"""缓存与训练撮合不得泄露未来价格或混入日常模拟账户。"""
from coinpilot_ai.market.cache import MarketCache
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.jobs import WorkQueue
from PyQt6.QtTest import QTest
import time
from coinpilot_ai.research.replay import ReplaySession
from coinpilot_ai.core.store import Store
from coinpilot_ai.trading.alerts import validate_rule
from coinpilot_ai.review.journal import aggregate
from coinpilot_ai.review.statistics import statistics


INST = "BTC-USDT-SWAP"
SPEC = {"instId": INST, "state": "live", "ctType": "linear", "settleCcy": "USDT",
        "ctVal": "0.01", "lotSz": "1", "minSz": "1", "tickSz": "0.1"}


def bars():
    values = [(100, 101, 99, 100), (110, 112, 108, 111), (120, 122, 118, 121)]
    return [[str((i+1)*900000), *(str(v) for v in row), "10", "0", "0", "1"]
            for i, row in enumerate(values)]


def test_cache_deduplicates_and_reports_gaps(tmp_path):
    cache = MarketCache(tmp_path / "market.db")
    rows = bars()
    assert cache.put(INST, "15m", rows) == 3
    cache.put(INST, "15m", rows[:1])
    assert cache.range(INST, "15m", 900000, 2700000) == CandleBuffer(rows)
    assert list(cache.gap_ranges(INST, "15m", 900000, 2700000)) == []
    assert cache.coverage(INST, "15m", 900000, 2700000)["segments"] == [
        {"begin": 900000, "end": 2700000, "source": "okx"}]
    assert cache.tail(INST, "15m", 2) == CandleBuffer(rows[-2:])
    assert cache.first_gap(INST, "15m", 900000, 2700000) is None
    cache.close()


def test_gap_detection_uses_timestamps_without_loading_candle_bodies(tmp_path, monkeypatch):
    cache = MarketCache(tmp_path / "market.db")
    try:
        cache.put(INST, "15m", [bars()[0], bars()[2]])
        monkeypatch.setattr(cache, "range", lambda *_: (_ for _ in ()).throw(AssertionError("不应解析 K 线")))
        assert cache.first_gap(INST, "15m", 900000, 2700000) == 1800000
        assert list(cache.gap_ranges(INST, "15m", 900000, 2700000)) == [(1800000, 1800000)]
    finally:
        cache.close()


def test_replay_order_waits_for_next_bar_and_resumes(tmp_path):
    store = Store(tmp_path / "business.db")
    session = ReplaySession(store, {INST: SPEC}, INST, "15m", bars())
    assert len(session.visible()) == 1
    payload = {"instId": INST, "tdMode": "cross", "side": "buy", "ordType": "market",
               "sz": "1", "clOrdId": "replay-test-1"}
    session.submit(payload)
    assert not store.list("fills", session.scope)
    assert not store.list("fills", "paper:local")
    assert session.step()
    fills = [record for _, record in store.list("fills", session.scope)]
    assert len(fills) == 1 and float(fills[0]["fillPx"]) == 110
    assert len(session.visible()) == 2
    restored = ReplaySession(store, {INST: SPEC}, INST, "15m", bars(), session.id)
    assert restored.cursor == 1
    assert len(restored.visible()) == 2
    store.close()


def test_replay_alerts_are_scoped_and_use_only_visible_bars(tmp_path):
    store = Store(tmp_path / "business.db")
    session = ReplaySession(store, {INST: SPEC}, INST, "15m", bars(), fee="0.001", slippage_bps="10")
    rule = validate_rule({"name": "训练价格", "instrument": INST, "mode": "all", "cooldown": 0,
                          "conditions": [{"metric": "price", "op": "above", "threshold": "105"}]})
    assert session.evaluate_rules([("price", rule)]) == []
    assert store.list("replay_alert", session.scope) == []
    assert session.step()
    events = session.evaluate_rules([("price", rule)])
    assert len(events) == 1 and float(events[0]["price"]) == 111
    assert len(session.visible()) == 2
    assert store.list("replay_alert", "paper:local") == []
    restored = ReplaySession(store, {INST: SPEC}, INST, "15m", bars(), session.id)
    assert restored.fee == "0.001" and restored.slippage_bps == "10"
    assert restored.evaluate_rules([("price", rule)]) == []
    store.close()


def test_replay_trailing_stop_does_not_assume_intrabar_order(tmp_path):
    store = Store(tmp_path / "business.db")
    rows = [["900000", "100", "101", "99", "100", "10", "0", "0", "1"],
            ["1800000", "100", "120", "95", "100", "10", "0", "0", "1"],
            ["2700000", "100", "105", "85", "90", "10", "0", "0", "1"]]
    session = ReplaySession(store, {INST: SPEC}, INST, "15m", rows)
    session.submit({"instId": INST, "tdMode": "cross", "side": "sell", "ordType": "trailing_stop",
                    "sz": "1", "trailOffset": "10", "clOrdId": "trail-test"})
    assert session.step()
    assert store.list("fills", session.scope) == []
    assert float(session.broker.state["pending"]["1"]["trailAnchor"]) == 120
    assert session.step()
    assert len(store.list("fills", session.scope)) == 1
    store.close()


def test_training_trade_statistics_stay_in_training_scope(tmp_path):
    store = Store(tmp_path / "business.db")
    session = ReplaySession(store, {INST: SPEC}, INST, "15m", bars(), fee="0")
    session.submit({"instId": INST, "tdMode": "cross", "side": "buy", "ordType": "market",
                    "sz": "1", "clOrdId": "open-train"})
    assert session.step()
    session.submit({"instId": INST, "tdMode": "cross", "side": "sell", "ordType": "market",
                    "sz": "1", "reduceOnly": True, "clOrdId": "close-train"})
    assert session.step()
    fills = [row for _, row in store.list("fills", session.scope)]
    orders = [row for _, row in store.list("orders", session.scope)]
    trades, _ = aggregate(fills, orders=orders)
    result = statistics(trades)
    assert result["trades"] == 1 and result["win_rate"] == 1
    assert store.list("fills", "paper:local") == []
    store.close()


def test_cache_numeric_schema_and_restart(tmp_path):
    path = tmp_path/"numeric.db"
    cache = MarketCache(path)
    cache.put(INST, "15m", bars(), source="download")
    columns = {row[1] for row in cache.db.execute("PRAGMA table_info(candles)")}
    assert "body" not in columns and {"open", "close", "volume_quote", "confirmed"} <= columns
    assert cache.first_gap(INST, "15m", 900000, 2700000) is None
    cache.close()
    cache = MarketCache(path)
    assert cache.tail(INST, "15m", 3) == CandleBuffer(bars())
    assert cache.coverage(INST, "15m", 900000, 2700000)["segments"][0]["source"] == "download"
    cache.close()


def test_segments_bridge_gaps_split_sources_and_ignore_unconfirmed(tmp_path):
    cache = MarketCache(tmp_path/"segments.db")
    try:
        data = bars()
        cache.put(INST, "15m", [data[0], data[2]])
        assert list(cache.gap_ranges(INST, "15m", 900000, 2700000)) == [(1800000, 1800000)]
        cache.put(INST, "15m", [data[1]])
        assert cache.db.execute("SELECT begin,end FROM candle_segments").fetchall() == [(900000, 2700000)]
        cache.put(INST, "15m", [data[1]], source="repair")
        assert [r["source"] for r in cache.coverage(INST, "15m", 900000, 2700000)["segments"]] == ["okx", "repair", "okx"]
        stale = data[1].copy()
        stale[4], stale[8] = "110", "0"
        cache.put(INST, "15m", [stale])
        assert cache.range(INST, "15m", 1800000, 1800000) == CandleBuffer([data[1]])
        assert cache.first_gap(INST, "15m", 900000, 2700000) is None
        new = data[2].copy()
        new[0], new[8] = "3600000", "0"
        cache.put(INST, "15m", [new])
        assert cache.first_gap(INST, "15m", 900000, 3600000) == 3600000
        new[8] = "1"
        cache.put(INST, "15m", [new])
        assert cache.first_gap(INST, "15m", 900000, 3600000) is None
    finally:
        cache.close()


def test_daily_segments_respect_exchange_timezone_offset(tmp_path):
    cache = MarketCache(tmp_path/"daily.db")
    try:
        data = bars()
        begin = 16*3600000
        for i, row in enumerate(data):
            row[0] = str(begin+i*86400000)
        cache.put(INST, "1D", data)
        assert list(cache.gap_ranges(INST, "1D", begin, begin+2*86400000)) == []
        assert cache.first_gap(INST, "1D", begin, begin+3*86400000) == begin+3*86400000
    finally:
        cache.close()


def test_history_loader_downloads_missing_page_instead_of_cached_tail(tmp_path, app):
    from types import SimpleNamespace
    from coinpilot_ai.market.cache import HistoryLoader
    cache = MarketCache(tmp_path/"loader.db")
    try:
        data = []
        for i in range(1000):
            row = bars()[0].copy()
            row[0] = str((i+1)*900000)
            data.append(row)
        cache.put(INST, "15m", data[:10]+data[11:])
        calls, finished = [], []
        api = SimpleNamespace(get=lambda path, done, params: calls.append((done, params)))
        queue = WorkQueue()
        loader = HistoryLoader(SimpleNamespace(api=api, closed=False, io=queue), cache)
        loader.finished.connect(lambda rows, error: finished.append((rows, error)))
        loader.load(INST, "15m", int(data[0][0]), int(data[-1][0]))
        deadline = time.monotonic()+5
        while not calls and time.monotonic() < deadline:
            QTest.qWait(1)
        assert len(calls) == 1
        assert int(calls[0][1]["after"]) == int(data[10][0])+300*900000
        calls[0][0]([data[10]], None)
        while not finished and time.monotonic() < deadline:
            QTest.qWait(1)
        assert len(finished) == 1 and finished[0][1] is None
        assert list(finished[0][0]) == list(CandleBuffer(data))
        finished[0][0].close()
        queue.close()
        assert not loader.active
    finally:
        cache.close()
