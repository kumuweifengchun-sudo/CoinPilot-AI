"""回测成交时序与逐笔价位分布的固定样本。"""
from coinpilot_ai.ui.qt import require
from decimal import Decimal

import pytest

from coinpilot_ai.research.backtest import run_backtest
from coinpilot_ai.market.microstructure import OrderBook, SequenceGap, TradeTape, volume_profile


def test_backtest_enters_next_open_and_uses_stop_on_ambiguous_bar():
    closes = [1, 1, 1, 3, 4]
    rows = []
    for i, close in enumerate(closes):
        high, low = (5, 2) if i == 4 else (close, close)
        rows.append([str((i+1)*900000), str(close), str(high), str(low), str(close), "10", "0", "0", "1"])
    strategy = {"bar": "15m", "direction": "long", "stop_percent": "1", "target_percent": "1",
                "risk_percent": "1", "conditions": [{"metric": "indicator", "indicator": {"kind": "MA", "params": {"period": 2}},
                                                    "op": "cross_above", "threshold": "1.5"}]}
    spec = {"ctType": "linear", "settleCcy": "USDT", "ctVal": "1", "lotSz": "1", "minSz": "1"}
    result = run_backtest(rows, strategy, spec, fee_rate="0", slippage_bps="0")
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["entry_time"] == int(rows[4][0])
    assert trade["entry"] == Decimal("4")
    assert trade["cause"] == "stop" and trade["ambiguous"]
    assert trade["net"] < 0


def test_book_sequence_gap_and_trade_profile():
    book = OrderBook()
    book.apply("snapshot", {"seqId": 10, "ts": "1000", "bids": [["100", "2"]], "asks": [["101", "3"]]})
    assert book.top()[1][0] == (Decimal("100"), Decimal("2"))
    book.apply("update", {"prevSeqId": 10, "seqId": 11, "ts": "2000", "bids": [["100", "0"]]})
    assert book.top()[1] == []
    with pytest.raises(SequenceGap):
        book.apply("update", {"prevSeqId": 9, "seqId": 12, "ts": "3000", "bids": []})
    assert book.sequence is None
    tape = TradeTape()
    trades = [{"tradeId": "1", "ts": "1000", "px": "100", "sz": "1", "side": "buy"},
              {"tradeId": "2", "ts": "2000", "px": "101", "sz": "3", "side": "buy"},
              {"tradeId": "3", "ts": "3000", "px": "102", "sz": "1", "side": "sell"}]
    assert tape.ingest(trades)
    assert not tape.ingest(trades)
    assert tape.cvd == Decimal("3")
    tape.ingest([{"tradeId": "4", "ts": "4000", "px": "103", "sz": "1"}])
    assert not tape.direction_available
    profile = volume_profile(tape.trades, "1")
    assert (require(profile)["poc"], require(profile)["val"], require(profile)["vah"]) == (Decimal("101"), Decimal("101"), Decimal("102"))


@pytest.mark.parametrize("kind", ["MA", "EMA", "BOLL", "MACD", "RSI", "ATR", "STOCHASTIC", "VWAP", "SUPERTREND", "VOLUME_MA"])
def test_streaming_backtest_matches_batch_indicator_oracle(kind, monkeypatch):
    import math
    from types import SimpleNamespace
    from coinpilot_ai.research import backtest
    from coinpilot_ai.market.indicators import calculate
    rows = [(900000*(i+1), 100+math.sin(i/8), 104., 96., 100+math.sin(i/8), 10., 0., 0., 1) for i in range(160)]
    strategy = {"bar": "15m", "direction": "long", "stop_percent": "1", "target_percent": "1",
                "risk_percent": "1", "conditions": [{"metric": "indicator", "indicator": {"kind": kind},
                                                       "op": "above", "threshold": "50"}]}
    spec = {"ctType": "linear", "settleCcy": "USDT", "ctVal": "1", "lotSz": "1", "minSz": "1"}
    actual = run_backtest(rows, strategy, spec)
    class BatchOracle:
        def __init__(self, spec, bar):
            self.spec, self.bar, self.rows = spec, bar, []
            self.state = SimpleNamespace(result=None)
        def push(self, row):
            self.rows.append(row)
            self.state.result = calculate(self.rows, self.spec, bar=self.bar)
    monkeypatch.setattr(backtest, "IndicatorStream", BatchOracle)
    assert actual == run_backtest(rows, strategy, spec)


def test_backtest_pages_history_and_limits_curve(tmp_path):
    from coinpilot_ai.market.history import HistoryData
    rows = HistoryData.create(((60000*(i+1), 100., 101., 99., 100., 10., 0., 0., 1) for i in range(12000)), 60000)
    strategy = {"bar": "1m", "direction": "long", "stop_percent": "1", "target_percent": "1",
                "risk_percent": "1", "conditions": [{"metric": "indicator", "indicator": {"kind": "MA"},
                                                       "op": "above", "threshold": "1000"}]}
    spec = {"ctType": "linear", "settleCcy": "USDT", "ctVal": "1", "lotSz": "1", "minSz": "1"}
    try:
        result = run_backtest(rows, strategy, spec)
        assert result["coverage"]["bars"] == 12000
        assert len(result["equity_curve"]) <= 4096
        assert result["equity_curve"][-1]["time"] == rows[-1][0]
        assert len(rows.pages) <= 2
        with pytest.raises(ValueError, match="取消"):
            run_backtest(rows, strategy, spec, cancelled=lambda: True)
    finally:
        rows.close()
