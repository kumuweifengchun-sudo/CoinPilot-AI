from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
import time
from unittest.mock import patch

from PyQt6.QtCore import QDateTime
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from coinpilot_ai.ai.context import WorkspaceSnapshot
from coinpilot_ai.ai.models import JsonValue
from coinpilot_ai.ai.providers.base import ToolCall
from coinpilot_ai.ai.tools.base import ToolContext, ToolResult
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.review.manual import EquitySnapshot, ManualFill, ManualTrade, calculate, reference
from coinpilot_ai.review.manual_widgets import AmountEditor, ManualEntry, set_time
from coinpilot_ai.review.page import ReviewPage
from coinpilot_ai.trading.panel import TradeController
from coinpilot_ai.ui.common import select_data


class EmptyVault:
    def read(self, key: str) -> None:
        return None


@pytest.fixture
def service(tmp_path: Path, app: QApplication) -> Iterator[CockpitService]:
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path / "manual.db", tmp_path / "icons",
                             vault=EmptyVault(), autostart=False)
    with patch.object(service.transport, "request", side_effect=AssertionError("记账不得联网")), \
         patch.object(service.trading, "prepare", side_effect=AssertionError("记账不得准备交易")), \
         patch.object(service.api, "post", side_effect=AssertionError("记账不得下单")):
        try:
            yield service
        finally:
            service.close()


def trade(book: str) -> ManualTrade:
    now = int(time.time() * 1000) - 10000
    return ManualTrade(book_id=book, fees_known=True, funding_known=True,
        fills=[ManualFill(time=now, price="50000", amount_mode="margin", amount="100", leverage="10"),
               ManualFill(time=now + 1000, action="close", price="51000", amount_mode="all")],
        before=EquitySnapshot(time=now - 1, equity="1000"), after=EquitySnapshot(time=now + 2000, equity="2000"))


def test_amount_mode_conversion_keeps_position_and_requires_leverage(app: QApplication) -> None:
    widget = AmountEditor()
    widget.set_product("swap")
    widget.price.setText("50000")
    widget.amount.setText("1000")
    select_data(widget.mode, "margin")
    assert widget.mode.currentData() == "notional"
    assert widget.amount.text() == "1000"
    widget.leverage.setText("10")
    select_data(widget.mode, "margin")
    assert widget.amount.text() == "100.00"
    select_data(widget.mode, "notional")
    assert float(widget.amount.text()) == 1000
    select_data(widget.mode, "margin")
    widget.set_product("spot")
    assert float(widget.amount.text()) == 1000
    assert widget.leverage.text() == ""
    widget.deleteLater()


def test_shared_entry_no_credentials_draft_isolation_and_review(service: CockpitService, app: QApplication) -> None:
    book = service.manual_ledger.create_book("手动", "示例交易所", "A")
    controller = TradeController(service)
    controller.size.setText("7")
    controller.reason.setText("原下单草稿")
    controller.entry_mode.setCurrentIndex(1)
    entry = controller.manual_entry
    entry.load_trade(trade(book))
    entry.save()
    assert "已保存" in entry.feedback.text()
    assert not service.account_connected
    saved_id = entry.trade.id
    entry.save()
    assert len(service.manual_ledger.trades(book)) == 1
    assert service.manual_ledger.get(book, saved_id).version == 2
    controller.entry_mode.setCurrentIndex(0)
    assert controller.size.text() == "7" and controller.reason.text() == "原下单草稿"
    controller.entry_mode.setCurrentIndex(1)
    assert entry.trade.id == saved_id
    assert calculate(entry.trade).net == 20
    page = ReviewPage(service)
    select_data(page.source, book)
    page.end.setDateTime(QDateTime.currentDateTime().addSecs(1))
    page.refresh()
    assert page.trade_table.rowCount() == 1
    assert "净盈亏 20" in page.stats_label.text()
    assert not page.sync_button.isEnabled()
    assert page.context()["manual_book_id"] == book
    page.tag_filter.setText("不匹配")
    page.refresh()
    assert page.trade_table.rowCount() == 0
    with pytest.raises(ValueError, match="没有"):
        page.context()
    page.deleteLater()
    controller.sidebar.deleteLater()
    controller.bottom.deleteLater()


def run_tool(service: CockpitService, name: str, arguments: dict[str, JsonValue], *, allowed: bool = True) -> ToolResult:
    chat = service.ai_chat
    assert chat is not None
    results: list[ToolResult] = []
    chat.registry.execute(ToolCall("manual-test", name, arguments),
                          ToolContext(chat.scope, "manual", time.time(), True, allowed), results.append)
    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        QTest.qWait(5)
    assert len(results) == 1
    return results[0]


def test_ai_manual_scope_permissions_and_cross_book_denial(service: CockpitService) -> None:
    ledger = service.manual_ledger
    first = ledger.create_book("一", "测试", "A")
    second = ledger.create_book("二", "测试", "B")
    saved = ledger.save(trade(first))
    other = ledger.save(trade(second))
    service.select_review_book(first)
    chat = service.ai_chat
    assert chat is not None
    first_conversation = chat.conversation.id
    assert WorkspaceSnapshot(time.time(), chat.scope, "manual").active_chart is None
    result = run_tool(service, "get_trade_review", {"trade_id": reference(first, saved.id)})
    assert result.ok and result.data["manual_book_id"] == first
    assert not run_tool(service, "get_trade_review", {"trade_id": reference(first, saved.id)}, allowed=False).ok
    assert not run_tool(service, "get_trade_review", {"trade_id": reference(second, other.id)}).ok
    assert not run_tool(service, "get_account", {}).ok
    assert not run_tool(service, "create_order_draft", {}).ok
    service.select_review_book(second)
    assert chat.conversation.id != first_conversation
    assert not run_tool(service, "get_trade_review", {"trade_id": reference(first, saved.id)}).ok
    service.select_review_book(first)
    assert chat.conversation.id == first_conversation
    service.select_review_book("")
    assert chat.scope == service.scope


def test_manual_report_requires_history_permission_and_stays_in_book(service: CockpitService) -> None:
    ledger = service.manual_ledger
    book = ledger.create_book("一", "测试", "A")
    saved = ledger.save(trade(book))
    record = {"id": "test", "name": "test", "protocol": "chat", "base_url": "http://127.0.0.1:1/v1",
              "model": "test", "allow_history": False}
    service.store.put("ai_service", "test", record)
    service.settings["scenes"] = {"review": {"service": "test"}}
    context = ledger.review(book, 0, int(time.time() * 1000), [saved.id])
    with pytest.raises(ValueError, match="读取权限"):
        service.ask_ai("review", context)
    record["allow_history"] = True
    service.store.put("ai_service", "test", record)
    with patch.object(service.ai, "ask", return_value="fake"):
        task = service.ask_ai("review", context)
        pending = service.ai_tasks[task]
        assert pending["scope"] == ledger.scope(book)
        assert pending["record"]["context"]["trades"][0]["id"] == saved.id
        saved.notes["reason"] = "稍后修改"
        ledger.save(saved)
        assert "reason" not in pending["record"]["context"]["trades"][0]["record"]["notes"]
        service.ai_tasks.clear()
