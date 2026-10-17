from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
import time
from unittest.mock import patch

from PyQt6.QtCore import QDateTime, QEvent, QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QToolButton
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
from coinpilot_ai.review.statistics import statistics
from coinpilot_ai.trading.panel import TradeController
from coinpilot_ai.ui.common import select_data
from coinpilot_ai.ui.qt import require


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
    assert page.stat_values["known_net"].text() == "+20.00"
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


def test_review_grouping_history_and_more_actions_keep_existing_context(
        service: CockpitService, app: QApplication) -> None:
    book = service.manual_ledger.create_book("复盘布局", "离线", "A")
    saved = service.manual_ledger.save(trade(book))
    report_key = service.store.append("report", {
        "time": time.time(), "status": "complete", "answer": "离线历史报告",
        "service": {"name": "模拟服务", "model": "offline"},
        "template": {"name": "复盘模板", "version": 1},
        "context": {"manual_book_id": book},
    }, service.manual_ledger.scope(book))
    page = ReviewPage(service)
    select_data(page.source, book)
    page.end.setDateTime(QDateTime.currentDateTime().addSecs(1))
    page.resize(1280, 900)
    page.show()
    app.processEvents()
    requests: list[tuple[str, str]] = []
    page.manual_requested.connect(lambda book_id, key: requests.append((book_id, key)))
    try:
        page.trade_table.selectRow(0)
        for key in ("direction", "instrument", "tag"):
            select_data(page.group_by, key)
            assert page.group_choice.currentWidget() is page.group_tables[key]
            assert page.context()["manual_book_id"] == book
        more = next(widget for widget in page.findChildren(QToolButton)
                    if widget.accessibleName() == "更多交易操作")
        menu = require(more.menu())
        assert [action.text() for action in menu.actions()] == [
            "编辑手动记录", "作废手动记录", "账户资金与流水", "交易图表快照"]
        menu.actions()[0].trigger()
        assert requests == [(book, saved.id)]
        page.detail_tabs.setCurrentIndex(1)
        page.ai_panel.setChecked(False)
        assert not page.ai_panel.body.isVisible()
        page.reports.itemClicked.emit(require(page.reports.item(0)))
        app.processEvents()
        assert page.ai_panel.isChecked() and page.ai_panel.body.isVisible()
        assert page.ai_panel.answer.toPlainText() == "离线历史报告"
        assert require(page.ai_panel.last_record)["id"] == report_key
        assert page.trade_table.rowCount() == 1
    finally:
        page.close()
        page.deleteLater()


@pytest.mark.parametrize("width", [1000, 1440])
def test_review_time_labels_stay_next_to_fields(
        service: CockpitService, app: QApplication, width: int) -> None:
    page = ReviewPage(service)
    page.resize(width, 900)
    page.show()
    app.processEvents()
    try:
        assert page.width() <= width
        for editor in (page.begin, page.end):
            label = next(label for label in require(editor.parentWidget()).findChildren(QLabel)
                         if label.buddy() is editor)
            assert editor.x() - label.geometry().right() - 1 == 4
    finally:
        page.close()
        page.deleteLater()


def test_review_vertical_drag_saves_height_and_keeps_panels_visible(
        service: CockpitService, app: QApplication) -> None:
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    page = ReviewPage(service)
    page.resize(1280, 900)
    page.show()
    app.processEvents()
    restored: ReviewPage | None = None
    try:
        splitter = page.records_splitter
        handle = require(splitter.handle(1))
        before = splitter.sizes()
        point = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(handle, point + QPoint(0, 70))
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        app.processEvents()
        saved = splitter.sizes()
        assert saved[0] > before[0] and saved[1] < before[1]
        assert service.store.get('chart_layout', 'review_records_details') == saved
        page.detail_tabs.setCurrentIndex(1)
        page.hide()
        page.show()
        app.processEvents()
        assert splitter.sizes() == saved
        restored = ReviewPage(service)
        restored.resize(1280, 900)
        restored.show()
        app.processEvents()
        assert all(abs(a - b) <= 1 for a, b in zip(restored.records_splitter.sizes(), saved))
        splitter.setSizes([0, sum(saved)])
        app.processEvents()
        assert all(size > 0 for size in splitter.sizes())
        splitter.setSizes([sum(saved), 0])
        app.processEvents()
        assert all(size > 0 for size in splitter.sizes())
    finally:
        if restored is not None:
            restored.close()
            restored.deleteLater()
        page.close()
        page.deleteLater()


@pytest.mark.parametrize('net, expected, tone', [
    ('25.50', '+25.50', 'positive'), ('-5.25', '-5.25', 'negative'),
    ('0', '0.00', 'neutral'), (None, '0.00', 'neutral')])
def test_review_metric_cards_preserve_values_missing_samples_and_error_reset(
        service: CockpitService, net: str | None, expected: str, tone: str) -> None:
    page = ReviewPage(service)
    rows: list[dict[str, object]] = [] if net is None else [
        {'id': 'offline', 'status': 'closed', 'start': 0, 'end': 60000,
         'net': net, 'note': {'risk_budget': '10'}}]
    try:
        page.show_statistics(statistics(rows))
        assert page.stat_values['known_net'].text() == expected
        assert page.stat_values['known_net'].property('tone') == tone
        assert page.stat_values['trades'].text() == ('0' if net is None else '1')
        if net is None:
            for key in ('win_rate', 'average_r', 'average_win', 'average_loss', 'average_duration_ms'):
                assert page.stat_values[key].text() == '—'
        else:
            assert page.stat_values['known_net'].toolTip() == f'{net} USDT'
            assert page.stat_values['average_duration_ms'].text() == '1.0 分钟'
        # 无效日期范围不能遗留上一轮的收益、胜率或盈亏颜色。
        page.begin.setDateTime(page.end.dateTime())
        assert all(value.text() == '—' for value in page.stat_values.values())
        assert page.stat_values['known_net'].property('tone') == 'neutral'
        assert all(table.rowCount() == 0 for table in page.group_tables.values())
    finally:
        page.deleteLater()


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
