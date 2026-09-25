"""缓存与训练撮合不得泄露未来价格或混入日常模拟账户。"""
from coinpilot_ai.market.cache import MarketCache
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
    assert cache.range(INST, "15m", 900000, 2700000) == rows
    assert cache.gaps(INST, "15m", 900000, 2700000) == []
    assert cache.coverage(INST, "15m", 900000, 2700000)["segments"] == [
        {"begin": 900000, "end": 2700000, "source": "okx"}]
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
    assert len(fills) == 1 and fills[0]["fillPx"] == "110"
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
    assert len(events) == 1 and events[0]["price"] == "111"
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
    assert session.broker.state["pending"]["1"]["trailAnchor"] == "120"
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
