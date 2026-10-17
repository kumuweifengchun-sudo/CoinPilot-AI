"""交易复盘页面：时间范围、交易统计、日志与 AI 报告。"""
import time

from PyQt6.QtCore import QDateTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDateTimeEdit, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from coinpilot_ai.ui.qt import require
from coinpilot_ai.core.store import encode
from coinpilot_ai.ui.common import button, combo, confirm, fill_table, select_data, selected_id, show_text, table, timestamp
from coinpilot_ai.trading.models import number
from .manual import reference
from .manual_widgets import AccountLedgerDialog
from .equity_curve import EquityCurve
from .snapshot_widgets import SnapshotGallery
from .ai_panel import AiPanel
from .panel import edit_journal_note
from .statistics import statistics, grouped_statistics


class ReviewPage(QWidget):
    chat_requested = pyqtSignal(str, str, str)
    manual_requested = pyqtSignal(str, str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.trades = []
        root = QVBoxLayout(self)
        source_controls = QHBoxLayout()
        self.source = combo([("", "当前交易账户")])
        self.product_filter = combo([("", "全部产品"), ("spot", "现货"), ("swap", "永续")])
        self.direction_filter = combo([("", "全部方向"), ("long", "多／买入"), ("short", "空")])
        self.instrument_filter, self.tag_filter = QLineEdit(), QLineEdit()
        self.instrument_filter.setPlaceholderText("交易对筛选")
        self.tag_filter.setPlaceholderText("策略标签筛选")
        for widget in (self.source, self.product_filter, self.direction_filter, self.instrument_filter, self.tag_filter):
            source_controls.addWidget(widget)
        root.addLayout(source_controls)
        controls = QHBoxLayout()
        self.begin, self.end = QDateTimeEdit(), QDateTimeEdit()
        self.begin.setDateTime(QDateTime.currentDateTime().addDays(-30))
        self.end.setDateTime(QDateTime.currentDateTime())
        for label, widget in (("起始", self.begin), ("结束", self.end)):
            widget.setDisplayFormat("yyyy-MM-dd HH:mm")
            widget.setCalendarPopup(True)
            controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(button("应用范围", self.refresh))
        self.sync_button = button("同步该范围历史", self.sync, True)
        controls.addWidget(self.sync_button)
        controls.addWidget(button("清除选择", lambda: self.trade_table.clearSelection()))
        root.addLayout(controls)
        manual_actions = QHBoxLayout()
        manual_actions.addWidget(button("补录交易", lambda: self.manual_requested.emit(str(self.source.currentData() or ""), "")))
        manual_actions.addWidget(button("编辑手动记录", self.edit_manual))
        manual_actions.addWidget(button("作废手动记录", self.void_manual))
        manual_actions.addWidget(button("账户资金与流水", self.account_ledger))
        manual_actions.addWidget(button("交易图表快照", self.view_snapshots))
        root.addLayout(manual_actions)
        self.coverage = QLabel("配置 OKX 账户后自动同步近 30 天，或选择范围手动同步。")
        self.coverage.setWordWrap(True)
        root.addWidget(self.coverage)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        self.trade_table = table(["开仓时间", "合约 / 方向", "状态", "已实现", "费用", "资金费", "已知净额"], readable=True)
        self.trade_table.setColumnWidth(0, 135)
        self.trade_table.setColumnWidth(1, 170)
        self.trade_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.trade_table.doubleClicked.connect(self.show_trade)
        layout.addWidget(self.trade_table, 2)
        layout.addWidget(QLabel("选中行则复盘所选交易；未选择则按时间范围。双击查看记录与数据缺口。"))
        layout.addWidget(button("编辑所选交易日志", self.edit_note))
        layout.addWidget(button("继续与 AI 讨论所选交易", self.discuss_trade))
        self.stats_label = QLabel()
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)
        self.manual_curve = EquityCurve(service.store, "manual")
        self.manual_curve.caption = "累计已实现净盈亏 · USDT（完整已平仓交易）"
        self.manual_curve.empty_text = "暂无完整已平仓交易"
        self.manual_curve.set_points([])
        self.manual_curve.setVisible(False)
        layout.addWidget(self.manual_curve)
        self.group_choice = QTabWidget()
        self.group_tables = {}
        for key, title in (("direction", "方向"), ("instrument", "合约"), ("tag", "标签")):
            group_table = table(["分组", "笔数", "胜率", "平均 R", "已知净额"], readable=True)
            group_table.setMaximumHeight(125)
            self.group_tables[key] = group_table
            self.group_choice.addTab(group_table, title)
        layout.addWidget(self.group_choice)
        self.reports = QListWidget()
        self.reports.itemClicked.connect(self.load_report)
        layout.addWidget(QLabel("历史报告（重新生成保留旧版）"))
        layout.addWidget(self.reports, 1)
        splitter.addWidget(left)
        self.ai_panel = AiPanel(service, "review", self.context)
        self.ai_panel.chat_requested.connect(self.discuss_report)
        splitter.addWidget(self.ai_panel)
        splitter.setSizes([600, 560])
        root.addWidget(splitter)
        self.reload_books()
        self.source.currentIndexChanged.connect(self.source_changed)
        for choice in (self.product_filter, self.direction_filter):
            choice.currentIndexChanged.connect(self.refresh)
        for edit in (self.instrument_filter, self.tag_filter):
            edit.editingFinished.connect(self.refresh)
        self.begin.dateTimeChanged.connect(self.refresh)
        self.end.dateTimeChanged.connect(self.refresh)
        service.updated.connect(self.manual_updated)
        self.refresh()

    def reload_books(self) -> None:
        current = self.source.currentData()
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem("当前交易账户", "")
        for book in self.service.manual_ledger.books():
            self.source.addItem(f"手动 · {book['name']} / {book['account']}", book["id"])
        select_data(self.source, current)
        self.source.blockSignals(False)

    def manual_updated(self, kind: str) -> None:
        if kind == "manual":
            self.reload_books()
            self.refresh()

    def source_changed(self) -> None:
        self.service.select_review_book(str(self.source.currentData() or ""))
        if self.ai_panel.task:
            self.service.cancel_ai(self.ai_panel.task)
        self.ai_panel.last_request = self.ai_panel.last_record = None
        self.ai_panel.answer.clear()
        self.refresh()

    def report_scope(self) -> str:
        book = self.source.currentData()
        return self.service.manual_ledger.scope(book) if book else self.service.scope

    def edit_manual(self) -> None:
        key, book = selected_id(self.trade_table), self.source.currentData()
        if key and book:
            self.manual_requested.emit(book, key)

    def view_snapshots(self) -> None:
        key, book = selected_id(self.trade_table), self.source.currentData()
        if not key or not book:
            self.coverage.setText("请在手动账本中选中一笔交易。")
            return
        trade = self.service.manual_ledger.get(book, key)
        if not trade.chart_snapshots:
            self.coverage.setText("该交易尚未保存图表快照，可编辑记录后采集。")
            return
        SnapshotGallery(trade.chart_snapshots, self).exec()

    def void_manual(self) -> None:
        key, book = selected_id(self.trade_table), self.source.currentData()
        if key and book and confirm(self, "作废手动交易", "记录及旧版本保留，作废后不再计入统计。继续？"):
            self.service.manual_ledger.void(book, key)
            self.service.updated.emit("manual")

    def account_ledger(self) -> None:
        book = self.source.currentData()
        if book:
            AccountLedgerDialog(self.service.manual_ledger, book, self).exec()
            self.service.updated.emit("manual")
        else:
            self.coverage.setText("请先选择手动账本。")

    def discuss_report(self, key: str) -> None:
        record = self.ai_panel.last_record
        book = record.get("context", {}).get("manual_book_id") if record else None
        if book:
            self.service.select_review_book(book)
        self.chat_requested.emit("report", reference(book, key) if book else key, "复盘报告")

    def range(self):
        begin, end = self.begin.dateTime().toSecsSinceEpoch(), self.end.dateTime().toSecsSinceEpoch()
        if begin >= end:
            raise ValueError("结束时间必须晚于起始时间")
        return begin, end

    def context(self):
        begin, end = self.range()
        keys = [require(self.trade_table.item(index.row(), 0)).data(Qt.ItemDataRole.UserRole) for index in require(self.trade_table.selectionModel()).selectedRows()]
        book = self.source.currentData()
        if book:
            selected = keys or [row["id"] for row in self.trades]
            if not selected:
                raise ValueError("当前筛选没有可复盘交易")
            return self.service.manual_ledger.review(book, begin * 1000, end * 1000, selected)
        return self.service.review_context(keys, begin, end)

    def refresh(self):
        try:
            begin, end = self.range()
            book = self.source.currentData()
            self.sync_button.setEnabled(not bool(book))
            self.manual_curve.setVisible(bool(book))
            if book:
                context = self.service.manual_ledger.review(book, begin * 1000, end * 1000)
                self.trades = context["trades"]
                costs = []
                self.coverage.setText("手动账本 · 按最终平仓时间统计；未平仓按开仓时间展示。未知费用排除完整统计；账户权益单独查看。")
            else:
                trades, costs = self.service.journal()
                self.trades = [row for row in trades if row["start"] <= end*1000 and (row["end"] or time.time()*1000) >= begin*1000]
            self.trades = [row for row in self.trades
                           if (not self.product_filter.currentData() or row.get("product", "swap") == self.product_filter.currentData())
                           and (not self.direction_filter.currentData() or row["direction"] == self.direction_filter.currentData())
                           and self.instrument_filter.text().strip().upper() in row["instrument"]
                           and (not self.tag_filter.text().strip() or self.tag_filter.text().strip() in row.get("note", {}).get("tags", []))]
            names = {"closed": "已平仓", "open": "未结束", "incomplete": "记录不完整"}
            fill_table(self.trade_table, [(t["id"], [timestamp(t["start"]/1000), t["instrument"]+" / "+t["direction"], names[t["status"]],
                t["realized"] if t["realized"] is not None else "暂无法计算",
                "未确认完整" if t.get("source") == "manual" and not t["record"]["fees_known"] else t["fees"],
                "未确认完整" if t.get("product") == "swap" and t.get("source") == "manual" and not t["record"]["funding_known"] else t["funding"],
                t["net"] if t["net"] is not None else "暂无法计算"]) for t in reversed(self.trades)])
            stats = statistics(self.trades)
            ratio = f"{stats['win_rate']:.1%}" if stats["win_rate"] is not None else "—"
            factor = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] is not None else "—"
            average = f"{stats['average_r']:+.2f}R" if stats["average_r"] is not None else "—"
            self.stats_label.setText(f"完整平仓 {stats['trades']} 笔 · 排除不完整 {stats['excluded']} 笔 · 胜率 {ratio} · "
                                     f"Profit Factor {factor} · 平均 {average}（{stats['r_samples']} 笔） · "
                                     f"最大已实现回撤 {stats['max_drawdown']} USDT\n"
                                     f"盈利 {stats['wins']} / 亏损 {stats['losses']} / 保本 {stats['breakeven']} · 净盈亏 {stats['known_net']} USDT\n"
                                     f"平均盈利 {stats['average_win'] if stats['average_win'] is not None else '—'} / 平均亏损 {stats['average_loss'] if stats['average_loss'] is not None else '—'} USDT · "
                                     f"平均持仓 {round(stats['average_duration_ms'] / 60000, 1) if stats['average_duration_ms'] is not None else '—'} 分钟")
            cumulative = number("0")
            points = [{"time": begin * 1000, "equity": "0"}]
            for trade in sorted((t for t in self.trades if t["status"] == "closed"), key=lambda t: (t["end"], t["id"])):
                cumulative += number(trade["net"])
                points.append({"time": trade["end"], "equity": str(cumulative)})
            self.manual_curve.set_points(points if len(points) > 1 else [])
            for key, group_table in self.group_tables.items():
                groups = grouped_statistics(self.trades, key)
                fill_table(group_table, [(name, [name, data["trades"],
                                                  f"{data['win_rate']:.1%}" if data["win_rate"] is not None else "—",
                                                  f"{data['average_r']:+.2f}R" if data["average_r"] is not None else "—",
                                                  str(data["known_net"])]) for name, data in groups.items()])
            runs = self.service.store.list("sync_run", self.service.scope, limit=1)
            if runs and not book:
                row = runs[0][1]
                failures = [name for name, result in row["results"].items() if not result["success"]]
                self.coverage.setText(f"最近同步请求：{timestamp(row['begin'])} — {timestamp(row['end'])} · " + ("分页完成" if row["complete"] else "存在缺口："+"、".join(failures)) + f" · 未归属费用 {len(costs)} 条" + (" · 超出接口回溯范围，较早记录未获取" if row.get("limited") else "") + "\n历史边界之前的仓位可能未知；范围内交易会展示完整已知过程。")
            self.refresh_reports()
        except (ValueError, KeyError) as exc:
            self.trades = []
            fill_table(self.trade_table, [])
            self.stats_label.clear()
            self.manual_curve.set_points([])
            self.coverage.setText(str(exc))

    def refresh_reports(self):
        self.reports.clear()
        for key, row in self.service.store.list("report", self.report_scope(), limit=200):
            item = QListWidgetItem(f"{timestamp(row['time'])} · {row['template']['name']} v{row['template']['version']} · {row['status']}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.reports.addItem(item)

    def load_report(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        record = self.service.store.get("report", key, scope=self.report_scope())
        if record:
            self.ai_panel.setChecked(True)
            self.ai_panel.show_record(dict(record, id=key))

    def show_trade(self):
        key = selected_id(self.trade_table)
        row = next((t for t in self.trades if t["id"] == key), None)
        if row:
            show_text(self, "交易记录与上下文", encode(row))

    def discuss_trade(self) -> None:
        key = selected_id(self.trade_table)
        if key:
            book = self.source.currentData()
            self.service.select_review_book(str(book or ""))
            self.chat_requested.emit("trade", reference(book, key) if book else key, "交易复盘")

    def edit_note(self):
        if self.source.currentData():
            self.edit_manual()
            return
        key = selected_id(self.trade_table)
        row = next((trade for trade in self.trades if trade["id"] == key), None)
        if row is None:
            return
        if edit_journal_note(self.service.store, self.service.scope, row, self):
            self.refresh()

    def sync(self):
        try:
            if self.source.currentData():
                raise ValueError("手动账本只录入本地事实，不同步交易所")
            if not self.service.account_connected:
                raise ValueError("请先配置当前环境的 OKX 账户")
            begin, end = self.range()
            if self.service.history.start(begin, end):
                self.coverage.setText("正在分页同步历史，完成后展示实际覆盖与缺口…")
            else:
                self.coverage.setText("已有同步任务进行中，请等待当前任务完成。")
        except ValueError as exc:
            self.coverage.setText(str(exc))
