"""交易复盘页面：时间范围、交易统计、日志与 AI 报告。"""
import time

from PyQt6.QtCore import QDateTime, Qt
from PyQt6.QtWidgets import (QAbstractItemView, QDateTimeEdit, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from coinpilot_ai.core.store import encode
from coinpilot_ai.ui.common import button, fill_table, selected_id, show_text, table, timestamp
from .ai_panel import AiPanel
from .panel import edit_journal_note
from .statistics import statistics, grouped_statistics


class ReviewPage(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.trades = []
        root = QVBoxLayout(self)
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
        controls.addWidget(button("同步该范围历史", self.sync, True))
        controls.addWidget(button("清除选择", lambda: self.trade_table.clearSelection()))
        root.addLayout(controls)
        self.coverage = QLabel("配置 OKX 账户后自动同步近 30 天，或选择范围手动同步。")
        self.coverage.setWordWrap(True)
        root.addWidget(self.coverage)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        self.trade_table = table(["开仓时间", "合约 / 方向", "状态", "已实现", "费用", "资金费", "已知净额"])
        self.trade_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.trade_table.doubleClicked.connect(self.show_trade)
        layout.addWidget(self.trade_table, 2)
        layout.addWidget(QLabel("选中行则复盘所选交易；未选择则按时间范围。双击查看记录与数据缺口。"))
        layout.addWidget(button("编辑所选交易日志", self.edit_note))
        self.stats_label = QLabel()
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)
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
        splitter.addWidget(self.ai_panel)
        splitter.setSizes([600, 560])
        root.addWidget(splitter)
        self.refresh()

    def range(self):
        begin, end = self.begin.dateTime().toSecsSinceEpoch(), self.end.dateTime().toSecsSinceEpoch()
        if begin >= end:
            raise ValueError("结束时间必须晚于起始时间")
        return begin, end

    def context(self):
        begin, end = self.range()
        keys = [self.trade_table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in self.trade_table.selectionModel().selectedRows()]
        return self.service.review_context(keys, begin, end)

    def refresh(self):
        try:
            begin, end = self.range()
            trades, costs = self.service.journal()
            self.trades = [row for row in trades if row["start"] <= end*1000 and (row["end"] or time.time()*1000) >= begin*1000]
            names = {"closed": "已平仓", "open": "未结束", "incomplete": "记录不完整"}
            fill_table(self.trade_table, [(t["id"], [timestamp(t["start"]/1000), t["instrument"]+" / "+t["direction"], names[t["status"]], t["realized"], t["fees"], t["funding"], t["net"]]) for t in reversed(self.trades)])
            stats = statistics(self.trades)
            ratio = f"{stats['win_rate']:.1%}" if stats["win_rate"] is not None else "—"
            factor = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] is not None else "—"
            average = f"{stats['average_r']:+.2f}R" if stats["average_r"] is not None else "—"
            self.stats_label.setText(f"完整平仓 {stats['trades']} 笔 · 排除不完整 {stats['excluded']} 笔 · 胜率 {ratio} · "
                                     f"Profit Factor {factor} · 平均 {average}（{stats['r_samples']} 笔） · "
                                     f"最大已实现回撤 {stats['max_drawdown']} USDT")
            for key, group_table in self.group_tables.items():
                groups = grouped_statistics(self.trades, key)
                fill_table(group_table, [(name, [name, data["trades"],
                                                  f"{data['win_rate']:.1%}" if data["win_rate"] is not None else "—",
                                                  f"{data['average_r']:+.2f}R" if data["average_r"] is not None else "—",
                                                  str(data["known_net"])]) for name, data in groups.items()])
            runs = self.service.store.list("sync_run", self.service.scope, limit=1)
            if runs:
                row = runs[0][1]
                failures = [name for name, result in row["results"].items() if not result["success"]]
                self.coverage.setText(f"最近同步请求：{timestamp(row['begin'])} — {timestamp(row['end'])} · " + ("分页完成" if row["complete"] else "存在缺口："+"、".join(failures)) + f" · 未归属费用 {len(costs)} 条" + (" · 超出接口回溯范围，较早记录未获取" if row.get("limited") else "") + "\n历史边界之前的仓位可能未知；范围内交易会展示完整已知过程。")
            self.refresh_reports()
        except (ValueError, KeyError) as exc:
            self.coverage.setText(str(exc))

    def refresh_reports(self):
        self.reports.clear()
        for key, row in self.service.store.list("report", self.service.scope, limit=200):
            item = QListWidgetItem(f"{timestamp(row['time'])} · {row['template']['name']} v{row['template']['version']} · {row['status']}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.reports.addItem(item)

    def load_report(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        record = self.service.store.get("report", key, scope=self.service.scope)
        if record:
            self.ai_panel.setChecked(True)
            self.ai_panel.show_record(dict(record, id=key))

    def show_trade(self):
        key = selected_id(self.trade_table)
        row = next((t for t in self.trades if t["id"] == key), None)
        if row:
            show_text(self, "交易记录与上下文", encode(row))

    def edit_note(self):
        key = selected_id(self.trade_table)
        row = next((trade for trade in self.trades if trade["id"] == key), None)
        if row is None:
            return
        if edit_journal_note(self.service.store, self.service.scope, row, self):
            self.refresh()

    def sync(self):
        try:
            if not self.service.account_connected:
                raise ValueError("请先配置当前环境的 OKX 账户")
            begin, end = self.range()
            if self.service.history.start(begin, end):
                self.coverage.setText("正在分页同步历史，完成后展示实际覆盖与缺口…")
            else:
                self.coverage.setText("已有同步任务进行中，请等待当前任务完成。")
        except ValueError as exc:
            self.coverage.setText(str(exc))
