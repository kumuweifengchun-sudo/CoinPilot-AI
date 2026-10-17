from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import sqlite3
import time

import pytest

from coinpilot_ai.core.store import Store
from coinpilot_ai.review.manual import (EquitySnapshot, ManualCost, ManualFill, ManualLedger, ManualTrade,
    calculate, journal_row, opening_quantity, read_review)
from coinpilot_ai.review.statistics import statistics

NOW = int(time.time() * 1000) - 100000


def sample(book: str = "") -> ManualTrade:
    return ManualTrade(book_id=book, fees_known=True, funding_known=True, fills=[
        ManualFill(time=NOW, price="50000", amount_mode="margin", amount="100", leverage="10"),
        ManualFill(time=NOW + 1000, action="close", price="51000", amount_mode="all")])


def test_margin_notional_and_pnl_do_not_multiply_leverage_twice() -> None:
    assert opening_quantity("50000", "margin", "100", "10") == Decimal("0.02")
    assert opening_quantity("50000", "notional", "1000") == Decimal("0.02")
    first = sample()
    second = deepcopy(first)
    second.fills[0].amount_mode, second.fills[0].amount = "notional", "1000"
    assert calculate(first) == calculate(second)
    assert calculate(first).net == 20
    first.notes["capital_note"] = "追加保证金 100，调整到 20 倍"
    assert calculate(first).net == 20
    first.direction = "short"
    assert calculate(first).net == -20


def test_weighted_average_partial_close_and_reopen_are_explicit() -> None:
    trade = sample()
    trade.mode = "detail"
    trade.fills = [ManualFill(time=NOW, price="100", amount_mode="notional", amount="100"),
                   ManualFill(time=NOW + 1, price="200", amount_mode="notional", amount="200"),
                   ManualFill(time=NOW + 2, action="close", price="180", amount_mode="percent", amount="50")]
    partial = calculate(trade)
    assert (partial.quantity, partial.realized, partial.status) == (1, 30, "open")
    trade.fills.append(ManualFill(time=NOW + 3, action="close", price="120", amount_mode="all"))
    assert calculate(trade).net == 0
    trade.fills.append(ManualFill(time=NOW + 4, price="100", amount_mode="base", amount="1"))
    with pytest.raises(ValueError, match="全部平仓"):
        calculate(trade)


def test_fee_funding_rebate_unknown_and_win_rate() -> None:
    trade = sample()
    trade.costs = [ManualCost(amount="2"), ManualCost(amount="-0.5"), ManualCost("funding", "-3")]
    assert calculate(trade).net == Decimal("15.5")
    missing = deepcopy(trade)
    missing.funding_known = False
    assert calculate(missing).net is None
    flat = sample()
    flat.fills[-1].price = "50000"
    losing = sample()
    losing.fills[-1].price = "49000"
    stats = statistics([journal_row(t) for t in (trade, missing, flat, losing)])
    assert (stats["trades"], stats["excluded"], stats["wins"], stats["losses"], stats["breakeven"]) == (3, 1, 1, 1, 1)
    assert stats["win_rate"] == 1 / 3
    assert stats["average_r"] is None
    trade.notes["risk_budget"] = "10"
    assert statistics([journal_row(trade)])["average_r"] == Decimal("1.55")
    trade.costs.append(ManualCost(amount="1", currency="BNB"))
    assert calculate(trade).net is None


@pytest.mark.parametrize("on_close", [False, True])
def test_spot_base_currency_fee_changes_quantity_without_double_charge(on_close: bool) -> None:
    trade = sample()
    trade.product = "spot"
    trade.fills[0].price, trade.fills[0].amount_mode, trade.fills[0].amount, trade.fills[0].leverage = "100", "base", "1", ""
    trade.fills[1].price = "110"
    trade.costs = [ManualCost(amount="0.01", currency="BTC", usdt="1.1" if on_close else "1",
                             fill_id=trade.fills[int(on_close)].id)]
    result = calculate(trade)
    assert result.quantity == 0
    assert result.net == Decimal("8.9")  # 实收 0.99 * 110 - 支出 100。
    assert Decimal(trade.fills[-1].quantity) == Decimal("0.99")


@pytest.mark.parametrize("mutation", ["price", "oversell", "future", "order", "spot_short", "summary_partial"])
def test_invalid_input_rejected(mutation: str) -> None:
    trade = sample()
    if mutation == "price":
        trade.fills[0].price = "NaN"
    elif mutation == "oversell":
        trade.fills[-1].amount_mode, trade.fills[-1].amount = "base", "100"
    elif mutation == "future":
        trade.fills[-1].time = int(time.time() * 1000) + 86400000
    elif mutation == "order":
        trade.fills[-1].time = NOW - 1
    elif mutation == "spot_short":
        trade.product, trade.direction = "spot", "short"
    else:
        trade.fills[-1].amount_mode, trade.fills[-1].amount = "percent", "50"
    with pytest.raises(ValueError):
        calculate(trade)


def test_persistence_revision_duplicates_conversion_void_and_account_isolation(tmp_path: Path) -> None:
    store = Store(tmp_path / "manual.db")
    ledger = ManualLedger(store)
    book = ledger.create_book("现货与合约", "测试交易所", "账户A")
    other = ledger.create_book("其他", "测试交易所", "账户B")
    original = sample(book)
    original.fills[0].external_id = "external-1"
    saved = ledger.save(original)
    assert saved.version == 1
    assert store.list("orders", "demo:test") == []
    duplicate = sample(book)
    duplicate.fills[0].external_id = "external-1"
    with pytest.raises(ValueError, match="成交号"):
        ledger.save(duplicate, allow_similar=True)
    store.close()


def test_revision_conflict_duplicates_and_conversion(tmp_path: Path) -> None:
    store = Store(tmp_path / "manual.db")
    ledger = ManualLedger(store)
    book = ledger.create_book("默认", "测试", "A")
    trade = sample(book)
    trade.fills[0].external_id = "first"
    saved = ledger.save(trade)
    with pytest.raises(ValueError, match="已被修改"):
        ledger.save(trade)
    duplicate = sample(book)
    duplicate.fills[0].external_id = "first"
    with pytest.raises(ValueError, match="成交号"):
        ledger.save(duplicate)
    duplicate.fills[0].external_id = ""
    with pytest.raises(ValueError, match="相似"):
        ledger.save(duplicate)
    saved.notes["reason"] = "事后补写"
    saved = ledger.save(saved)
    assert len(store.list("manual_revision", ledger.scope(book))) == 1
    saved.mode = "detail"
    with pytest.raises(ValueError, match="核对汇总"):
        ledger.save(saved)
    saved = ledger.save(saved, allow_conversion=True)
    assert saved.version == 3
    ledger.void(book, saved.id)
    assert ledger.trades(book) == []
    assert len(ledger.trades(book, include_voided=True)) == 1
    store.close()
    reopened = Store(tmp_path / "manual.db")
    assert ManualLedger(reopened).get(book, saved.id).voided
    reopened.close()


def test_account_snapshots_transfers_and_unrelated_equity_do_not_change_trade_pnl(tmp_path: Path) -> None:
    store = Store(tmp_path / "account.db")
    ledger = ManualLedger(store)
    book = ledger.create_book("账户", "测试", "A")
    other = ledger.create_book("账户", "测试", "B")
    trade = sample(book)
    trade.before = EquitySnapshot(time=NOW - 1, equity="1000")
    trade.after = EquitySnapshot(time=NOW + 2000, equity="2000")
    saved = ledger.save(trade)
    ledger.add_cashflow(book, NOW + 500, "500")
    ledger.add_cashflow(book, NOW + 600, "-100")
    ledger.add_cashflow(other, NOW + 600, "9999")
    assert calculate(saved).net == 20
    assert ledger.account_change(book, NOW - 1, NOW + 2000)["adjusted"] is None
    assert ledger.account_change(book, NOW - 1, NOW + 2000, complete=True)["adjusted"] == "600"
    assert ledger.account_change(other, NOW - 1, NOW + 2000)["change"] is None
    overlap = sample(book)
    overlap.fills[0].time += 10
    overlap.before, overlap.after = trade.before, trade.after
    ledger.save(overlap)
    assert len(store.list("manual_equity", ledger.scope(book))) == 2
    assert len(read_review(store.db, other, 0, NOW + 9000)["trades"]) == 0
    with pytest.raises(ValueError, match="不存在"):
        read_review(store.db, other, 0, NOW + 9000, [saved.id])
    # 只按结束时间选择，但完整开仓仍参与计算。
    context = read_review(store.db, book, NOW + 999, NOW + 1000)
    assert context["statistics"]["known_net"] == 40
    ledger.correct_snapshot(book, NOW + 2000, "2100")
    assert ledger.account_change(book, NOW - 1, NOW + 2000, complete=True)["adjusted"] == "700"
    changed = ledger.get(book, saved.id)
    assert changed.after is not None and changed.after.equity == "2100"
    assert changed.version == 2 and calculate(changed).net == 20
    with pytest.raises(ValueError, match="已被修改"):
        ledger.save(saved)
    store.close()


def test_attachment_copy_query_limit_and_conversion_mismatch(tmp_path: Path) -> None:
    store = Store(tmp_path / "attachments.db")
    ledger = ManualLedger(store)
    book = ledger.create_book("截图", "测试", "A")
    source = tmp_path / "image.png"
    # 存储测试只检查原始字节保留；图像渲染由 QA 检查。
    source.write_bytes(b"example image bytes")
    trade = sample(book)
    trade.attachments = [str(source)]
    saved = ledger.save(trade)
    assert Path(saved.attachments[0]).read_bytes() == source.read_bytes()
    assert Path(saved.attachments[0]).parent.name == trade.id
    converted = deepcopy(saved)
    converted.mode = "detail"
    converted.fills[-1].price = "52000"
    with pytest.raises(ValueError, match="不一致"):
        ledger.save(converted, allow_conversion=True)
    second = sample(book)
    second.fills[0].time += 10
    ledger.save(second)
    with pytest.raises(ValueError, match="过多"):
        read_review(store.db, book, 0, NOW + 2000, limit=1)
    plan = store.db.execute("EXPLAIN QUERY PLAN SELECT body FROM records WHERE scope=? AND kind='manual_trade' AND json_valid(body) "
                            "AND json_extract(body,'$.review_time') BETWEEN ? AND ? ORDER BY json_extract(body,'$.review_time'),id LIMIT 5",
                            (ledger.scope(book), 0, NOW + 2000)).fetchall()
    assert any("manual_trade_review_time" in r[3] for r in plan)
    store.close()


def test_missing_opening_is_preserved_but_not_fabricated(tmp_path: Path) -> None:
    store = Store(tmp_path / "missing.db")
    ledger = ManualLedger(store)
    book = ledger.create_book("缺口", "测试", "A")
    partial = ManualTrade(book_id=book, mode="detail", fees_known=True, funding_known=True,
        fills=[ManualFill(time=NOW + 1000, action="close", price="51000", amount_mode="base", amount="0.02")])
    saved = ledger.save(partial)
    result = calculate(saved)
    assert result.net is None and result.realized is None and result.quantity is None
    assert statistics([journal_row(saved)])["excluded"] == 1
    saved.fills.insert(0, ManualFill(time=NOW, price="50000", amount_mode="notional", amount="1000"))
    assert calculate(ledger.save(saved)).net == 20
    store.close()
