import json
from pathlib import Path
import sqlite3
import time

from PyQt6.QtWidgets import QApplication
import pytest

from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.core.store import Store
from coinpilot_ai.review.query import FILL_TIME, JournalQuery
from coinpilot_ai.ai.models import JsonValue
from coinpilot_ai.ai.providers.base import ToolCall
from coinpilot_ai.ai.tools.base import ToolContext, ToolResult
from PyQt6.QtTest import QTest


class EmptyVault:
    def read(self, key: str) -> None:
        return None


def fill(identity: int, stamp: int, *, instrument: str = "BTC-USDT-SWAP", closing: bool = False) -> dict[str, JsonValue]:
    return {"billId": str(identity), "tradeId": str(identity), "ordId": str(identity), "instId": instrument,
            "fillTime": str(stamp), "ts": str(stamp), "side": "sell" if closing else "buy", "posSide": "long",
            "mgnMode": "cross", "fillSz": "1", "fillPnl": "10" if closing else "0", "fee": "-1", "feeCcy": "USDT"}


def test_indexed_window_filters_scope_time_and_instrument_before_decoding(tmp_path: Path) -> None:
    store = Store(tmp_path / "business.sqlite3")
    try:
        rows = [("demo:test", "fills", str(i), json.dumps(fill(i, i * 1000, instrument="ETH-USDT-SWAP" if i % 2 else "BTC-USDT-SWAP")), time.time())
                for i in range(10000)]
        store.db.executemany("INSERT INTO records VALUES(?,?,?,?,?)", rows)
        store.db.commit()
        query = JournalQuery(store.db)
        selected, more = query.window("fills", "demo:test", 9000000, 9999000, instrument="BTC-USDT-SWAP", limit=5)
        assert [key for key, _ in selected] == ["9998", "9996", "9994", "9992", "9990"]
        assert more and query.rows_read == 6
        plan = store.db.execute(f"EXPLAIN QUERY PLAN SELECT id,body FROM records WHERE scope=? AND kind='fills' AND json_valid(body) "
                                 f"AND json_extract(body,'$.instId')=? AND {FILL_TIME} BETWEEN ? AND ? "
                                 f"ORDER BY {FILL_TIME} DESC,id DESC LIMIT ?", ("demo:test", "BTC-USDT-SWAP", 9000000, 9999000, 6)).fetchall()
        assert any("journal_fills_instrument_time" in row[3] for row in plan)
        assert query.window("fills", "live:other", 9000000, 9999000, limit=5) == ([], False)
        with pytest.raises(ValueError, match="180 天"):
            query.window("fills", "demo:test", 0, 181 * 86400000)
    finally:
        store.close()


def run_tool(service: CockpitService, name: str, arguments: dict[str, JsonValue], app: QApplication) -> ToolResult:
    chat = service.ai_chat
    assert chat is not None
    results: list[ToolResult] = []
    chat.registry.execute(ToolCall("test", name, arguments), ToolContext(service.scope, service.environment, time.time(), False, True), results.append)
    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        QTest.qWait(5)
    assert len(results) == 1
    return results[0]


def test_history_limit_five_does_not_load_or_aggregate_all_history(tmp_path: Path, app: QApplication) -> None:
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path / "business.sqlite3", tmp_path / "icons",
                             vault=EmptyVault(), autostart=False)
    try:
        now = int(time.time())
        records: list[tuple[str, str, str, str, float]] = []
        for i in range(10000):
            row = fill(i, (now - 240 * 86400 + i) * 1000, instrument="ETH-USDT-SWAP")
            records.append((service.scope, "fills", str(i), json.dumps(row), now))
        for i in range(1000):
            for closing in (False, True):
                identity = 20000 + i * 2 + int(closing)
                stamp = (now - 150000 + i * 120 + int(closing) * 60) * 1000
                records.append((service.scope, "fills", str(identity), json.dumps(fill(identity, stamp, closing=closing)), now))
                records.append((service.scope, "orders", str(identity), json.dumps({"ordId": str(identity), "tdMode": "cross"}), now))
        service.store.db.executemany("INSERT INTO records VALUES(?,?,?,?,?)", records)
        service.store.db.commit()
        result = run_tool(service, "get_trade_history", {"instrument": "BTC-USDT-SWAP", "begin": now - 7 * 86400, "limit": 5}, app)
        assert result.ok
        trades, query = result.data["trades"], result.data["query"]
        assert isinstance(trades, list) and len(trades) == 5
        assert isinstance(query, dict) and query["fills_read"] == 200 and query["truncated"] is True
        assert all(isinstance(row, dict) and row["instrument"] == "BTC-USDT-SWAP" for row in trades)
        assert result.data["has_more"] is True
        too_large = run_tool(service, "get_trade_review", {"begin": now - 181 * 86400}, app)
        assert not too_large.ok and "180 天" in too_large.warnings[0]
    finally:
        service.close()
        service.deleteLater()
        app.processEvents()


def test_bounded_trade_review_preserves_decimal_funding_notes_and_private_scope(tmp_path: Path, app: QApplication) -> None:
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path / "business.sqlite3", tmp_path / "icons", vault=EmptyVault(), autostart=False)
    try:
        now = int(time.time())
        opening, closing = (now - 120) * 1000, (now - 60) * 1000
        for identity, stamp, closes in ((1, opening, False), (2, closing, True)):
            service.store.put("fills", str(identity), fill(identity, stamp, closing=closes), service.scope)
            service.store.put("orders", str(identity), {"ordId": str(identity), "tdMode": "cross"}, service.scope)
            service.store.put("local_order", "local-" + str(identity), {"order_id": str(identity), "draft": {"reason": "记录理由"}}, service.scope)
        service.store.put("fills", "999", fill(999, opening), "live:other")
        service.store.put("bills", "funding", {"billId": "funding", "instId": "BTC-USDT-SWAP", "type": "8", "ts": str(opening + 1000),
                                              "ccy": "USDT", "balChg": "-0.25"}, service.scope)
        service.store.put("journal_note", "1:long", {"reason": "手动记录"}, service.scope)
        result = run_tool(service, "get_trade_review", {"trade_id": "1:long", "limit": 5}, app)
        assert result.ok
        trades = result.data["trades"]
        assert isinstance(trades, list) and len(trades) == 1
        trade = trades[0]
        assert isinstance(trade, dict) and trade["net"] == "7.75" and trade["funding"] == "-0.25"
        assert trade["note"] == {"reason": "手动记录"}
        assert isinstance(trade["recorded_context"], list) and len(trade["recorded_context"]) == 2
        cut = run_tool(service, "get_trade_review", {"begin": now - 100, "end": now - 30, "limit": 5}, app)
        assert cut.ok and "查询起点之前" in str(cut.data)
        statistics = cut.data["statistics"]
        assert isinstance(statistics, dict) and statistics["complete_closed"] == 0
    finally:
        service.close()
        service.deleteLater()
        app.processEvents()
