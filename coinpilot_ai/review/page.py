"""交易复盘页面：时间范围、交易统计、日志与 AI 报告。"""
import time

from PyQt6.QtCore import QDateTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDateTimeEdit, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QTabWidget, QToolButton, QVBoxLayout, QWidget)

from coinpilot_ai.ui.qt import require
from coinpilot_ai.ui.theme import SPACING_SM, SPACING_XS
from coinpilot_ai.charts.panel import SavedSplitter
from coinpilot_ai.core.store import encode
from coinpilot_ai.ui.common import button, combo, confirm, fill_table, select_data, selected_id, show_text, table, timestamp
from coinpilot_ai.trading.models import number
from .manual import reference
from .manual_widgets import AccountLedgerDialog
from .equity_curve import EquityCurve
from .snapshot_widgets import SnapshotGallery
from .ai_panel import AiPanel
from .panel import edit_journal_note
from .statistics import Statistics, statistics, grouped_statistics


class ReviewPage(QWidget):
    chat_requested = pyqtSignal(str, str, str)
    manual_requested = pyqtSignal(str, str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.trades = []
        self.setObjectName("reviewPage")
        root = QVBoxLayout(self)
        filters, filter_layout = self.card()
        source_controls = QHBoxLayout()
        self.source = combo([("", "当前交易账户")])
        self.product_filter = combo([("", "全部产品"), ("spot", "现货"), ("swap", "永续")])
        self.direction_filter = combo([("", "全部方向"), ("long", "多／买入"), ("short", "空")])
        self.instrument_filter, self.tag_filter = QLineEdit(), QLineEdit()
        self.instrument_filter.setPlaceholderText("交易对筛选")
        self.tag_filter.setPlaceholderText("策略标签筛选")
        for widget in (self.source, self.product_filter, self.direction_filter, self.instrument_filter, self.tag_filter):
            source_controls.addWidget(widget)
        filter_layout.addLayout(source_controls)
        controls = QHBoxLayout()
        self.begin, self.end = QDateTimeEdit(), QDateTimeEdit()
        self.begin.setDateTime(QDateTime.currentDateTime().addDays(-30))
        self.end.setDateTime(QDateTime.currentDateTime())
        for text, widget in (("起始时间", self.begin), ("结束时间", self.end)):
            widget.setDisplayFormat("yyyy-MM-dd HH:mm")
            widget.setCalendarPopup(True)
            widget.setFixedWidth(176)
            field = QWidget()
            field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            pair = QHBoxLayout(field)
            pair.setContentsMargins(0, 0, 0, 0)
            pair.setSpacing(SPACING_XS)
            label = QLabel(text)
            label.setBuddy(widget)
            pair.addWidget(label)
            pair.addWidget(widget)
            controls.addWidget(field)
        controls.addWidget(button("应用范围", self.refresh))
        self.sync_button = button("同步该范围历史", self.sync, True)
        controls.addWidget(self.sync_button)
        self.coverage = QLabel("配置 OKX 账户后自动同步近 30 天，或选择范围手动同步。")
        self.coverage.setObjectName("muted")
        self.coverage.setWordWrap(True)
        self.coverage.setMinimumWidth(0)
        controls.addSpacing(SPACING_SM)
        controls.addWidget(self.coverage, 1)
        filter_layout.addLayout(controls)
        root.addWidget(filters)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.records_splitter = SavedSplitter(Qt.Orientation.Vertical, service, "review_records_details")
        self.records_splitter.setChildrenCollapsible(False)
        records, records_layout = self.card()
        record_header = QHBoxLayout()
        title = QLabel("交易记录")
        title.setObjectName("reviewSectionTitle")
        record_header.addWidget(title)
        record_header.addStretch()
        record_header.addWidget(button("清除选择", lambda: self.trade_table.clearSelection()))
        record_header.addWidget(button("补录交易", lambda: self.manual_requested.emit(str(self.source.currentData() or ""), "")))
        more = QToolButton()
        more.setText("更多操作")
        more.setAccessibleName("更多交易操作")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        for text, callback in (("编辑手动记录", self.edit_manual), ("作废手动记录", self.void_manual),
                               ("账户资金与流水", self.account_ledger), ("交易图表快照", self.view_snapshots)):
            menu.addAction(text, callback)
        more.setMenu(menu)
        record_header.addWidget(more)
        records_layout.addLayout(record_header)
        self.trade_table = table(["开仓时间", "合约 / 方向", "状态", "已实现", "费用", "资金费", "已知净额"], readable=True)
        self.trade_table.setObjectName("reviewTable")
        self.trade_table.setColumnWidth(0, 135)
        self.trade_table.setColumnWidth(1, 170)
        self.trade_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.trade_table.doubleClicked.connect(self.show_trade)
        records_layout.addWidget(self.trade_table, 1)
        selection_actions = QHBoxLayout()
        hint = QLabel("可多选 · 双击查看详情")
        hint.setObjectName("muted")
        selection_actions.addWidget(hint)
        selection_actions.addStretch()
        selection_actions.addWidget(button("编辑日志", self.edit_note))
        selection_actions.addWidget(button("与 AI 讨论", self.discuss_trade))
        records_layout.addLayout(selection_actions)
        self.records_splitter.addWidget(records)
        details, details_layout = self.card()
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setObjectName("reviewDetails")
        summary = QWidget()
        summary.setObjectName("reviewSummary")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(0, SPACING_SM, 0, 0)
        self.stat_values: dict[str, QLabel] = {}
        highlights = QHBoxLayout()
        for key, title in (("known_net", "已知净盈亏 · USDT"), ("win_rate", "胜率"),
                           ("trades", "完整平仓 · 笔")):
            tile = QFrame()
            tile.setObjectName("reviewMetricTile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setSpacing(SPACING_XS)
            caption = QLabel(title)
            caption.setObjectName("reviewMetricCaption")
            value = QLabel("—")
            value.setObjectName("reviewMetricValue")
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            tile_layout.addWidget(caption)
            tile_layout.addWidget(value)
            highlights.addWidget(tile, 1)
            self.stat_values[key] = value
        summary_layout.addLayout(highlights)
        breakdown = QGridLayout()
        for index, (key, title) in enumerate((("profit_factor", "盈亏因子"), ("average_r", "平均 R"),
                ("max_drawdown", "最大已实现回撤"), ("average_win", "平均盈利"),
                ("average_loss", "平均亏损"), ("average_duration_ms", "平均持仓"))):
            pair = QHBoxLayout()
            pair.setSpacing(SPACING_XS)
            caption = QLabel(title)
            caption.setObjectName("reviewMetricCaption")
            value = QLabel("—")
            value.setObjectName("reviewDetailValue")
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pair.addWidget(caption)
            pair.addWidget(value, 1)
            breakdown.addLayout(pair, index // 3, index % 3)
            breakdown.setColumnStretch(index % 3, 1)
            self.stat_values[key] = value
        summary_layout.addLayout(breakdown)
        self.stats_label = QLabel()
        self.stats_label.setObjectName("reviewStatistics")
        self.stats_label.setWordWrap(True)
        summary_layout.addWidget(self.stats_label)
        self.manual_curve = EquityCurve(service.store, "manual")
        self.manual_curve.caption = "累计已实现净盈亏 · USDT（完整已平仓交易）"
        self.manual_curve.empty_text = "暂无完整已平仓交易"
        self.manual_curve.set_points([])
        self.manual_curve.setVisible(False)
        summary_layout.addWidget(self.manual_curve)
        grouping = QHBoxLayout()
        grouping.addWidget(QLabel("分组统计"))
        grouping.addStretch()
        self.group_by = combo([("direction", "按方向"), ("instrument", "按合约"), ("tag", "按标签")])
        grouping.addWidget(self.group_by)
        summary_layout.addLayout(grouping)
        self.group_choice = QStackedWidget()
        self.group_by.currentIndexChanged.connect(self.group_choice.setCurrentIndex)
        self.group_tables = {}
        for key in ("direction", "instrument", "tag"):
            group_table = table(["分组", "笔数", "胜率", "平均 R", "已知净额"], readable=True)
            group_table.setObjectName("reviewTable")
            self.group_tables[key] = group_table
            self.group_choice.addWidget(group_table)
        summary_layout.addWidget(self.group_choice, 1)
        summary_scroll = QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setFrameShape(QFrame.Shape.NoFrame)
        summary_scroll.setWidget(summary)
        self.detail_tabs.addTab(summary_scroll, "统计概览")
        report_page = QWidget()
        report_layout = QVBoxLayout(report_page)
        report_layout.setContentsMargins(0, SPACING_SM, 0, 0)
        report_hint = QLabel("选择报告在右侧查看；重新生成会保留旧版。")
        report_hint.setObjectName("muted")
        report_hint.setWordWrap(True)
        report_layout.addWidget(report_hint)
        self.reports = QListWidget()
        self.reports.setObjectName("reviewReports")
        self.reports.itemClicked.connect(self.load_report)
        report_layout.addWidget(self.reports, 1)
        self.detail_tabs.addTab(report_page, "历史报告")
        details_layout.addWidget(self.detail_tabs)
        self.records_splitter.addWidget(details)
        self.records_splitter.setStretchFactor(0, 1)
        self.records_splitter.setStretchFactor(1, 1)
        self.records_splitter.restore([240, 480])
        handle = require(self.records_splitter.handle(1))
        handle.setToolTip("上下拖动，调整交易记录与统计概览的高度")
        handle.setAccessibleName("交易记录与统计概览高度分隔条")
        splitter.addWidget(self.records_splitter)
        self.ai_panel = AiPanel(service, "review", self.context)
        self.ai_panel.chat_requested.connect(self.discuss_report)
        splitter.addWidget(self.ai_panel)
        splitter.setSizes([720, 560])
        root.addWidget(splitter, 1)
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

    def card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(self)
        card.setObjectName("reviewCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
        return card, layout

    def show_statistics(self, stats: Statistics | None) -> None:
        """仅格式化既有统计，不把缺失样本当作零收益或零胜率。"""
        values = {key: "—" for key in self.stat_values}
        tone = "neutral"
        if stats is not None:
            net = stats["known_net"]
            values.update(
                known_net=f"{net:+,.2f}" if net else "0.00",
                trades=str(stats["trades"]),
                win_rate=f"{stats['win_rate']:.1%}" if stats["win_rate"] is not None else "—",
                profit_factor=f"{stats['profit_factor']:.2f}" if stats["profit_factor"] is not None else "—",
                average_r=f"{stats['average_r']:+.2f}R" if stats["average_r"] is not None else "—",
                max_drawdown=f"{stats['max_drawdown']:,.2f}",
                average_win=f"{stats['average_win']:,.2f}" if stats["average_win"] is not None else "—",
                average_loss=f"{stats['average_loss']:,.2f}" if stats["average_loss"] is not None else "—",
                average_duration_ms=f"{stats['average_duration_ms'] / 60000:.1f} 分钟"
                    if stats["average_duration_ms"] is not None else "—")
            tone = "positive" if net > 0 else "negative" if net < 0 else "neutral"
            self.stats_label.setText(f"盈利 {stats['wins']} · 亏损 {stats['losses']} · 保本 {stats['breakeven']}"
                                    f"    排除未完整平仓 {stats['excluded']} 笔 · R 样本 {stats['r_samples']} 笔")
        else:
            self.stats_label.clear()
        amounts = {key: value for key, value in (
            ("known_net", stats["known_net"]), ("max_drawdown", stats["max_drawdown"]),
            ("average_win", stats["average_win"]), ("average_loss", stats["average_loss"]))} if stats is not None else {}
        for key, label in self.stat_values.items():
            label.setText(values[key])
            amount = amounts.get(key)
            label.setToolTip(f"{amount} USDT" if amount is not None else
                             values[key] if values[key] != "—" else "暂无可计算样本")
        net_label = self.stat_values["known_net"]
        net_label.setProperty("tone", tone)
        style = require(net_label.style())
        style.unpolish(net_label)
        style.polish(net_label)
        net_label.update()

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
            self.show_statistics(stats)
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
            self.show_statistics(None)
            for group_table in self.group_tables.values():
                fill_table(group_table, [])
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
