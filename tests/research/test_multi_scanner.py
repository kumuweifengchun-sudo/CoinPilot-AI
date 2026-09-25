"""全市场扫描的确定性判断。"""
import pytest
from coinpilot_ai.research.scanner import matches, parse_tickers, scan_metrics


def test_scanner_distinguishes_unavailable_from_nonmatching():
    rows = []
    for i in range(97):
        close = 100 if i < 96 else 105
        volume = 10 if i < 96 else 30
        rows.append([str((i+1)*900000), str(close), str(close+1), str(close-1),
                     str(close), str(volume), "0", "0", "1"])
    metrics = scan_metrics(rows)
    assert metrics["change"] == pytest.approx(5)
    assert metrics["volume_ratio"] == 3
    assert matches(metrics, {"change_min": 3, "volume_min": 2}) is True
    assert matches(metrics, {"rsi_max": 30}) is False
    assert matches(None, {"rsi_max": 30}) is None
    rows[-1][0] = str(200*900000)
    assert scan_metrics(rows) is None


def test_batch_ticker_rejects_other_and_stale_contracts():
    specs = {"BTC-USDT-SWAP": {}}
    rows = [{"instId": "BTC-USDT-SWAP", "last": "105", "open24h": "100", "ts": "100000", "vol24h": "10"},
            {"instId": "ETH-USDT-SWAP", "last": "200", "open24h": "100", "ts": "100000"}]
    result = parse_tickers(rows, specs, 100)
    assert result["BTC-USDT-SWAP"]["change24h"] == pytest.approx(5)
    assert "ETH-USDT-SWAP" not in result
    assert parse_tickers(rows, specs, 200) == {}
