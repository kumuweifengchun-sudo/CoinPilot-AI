"""桌面工作台主窗口。关闭仅隐藏，后台服务由应用持有。"""
import time

from PyQt6.QtCore import QDateTime, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDateTimeEdit, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from .chart_panel import ChartPanel, SavedSplitter
from .store import encode
from .ui_ai import AiPanel
from .ui_common import button, fill_table, selected_id, show_text, table, timestamp
from .ui_settings import SettingsPage
from .ui_trade import TradePage
from ..icons import icon, set_button_icon
from ..theme import color, events, style_sheet
from .watchlist import WatchlistDelegate


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

    def sync(self):
        try:
            if not self.service.api.credentials:
                raise ValueError("请先配置当前环境的 OKX 账户")
            begin, end = self.range()
            if self.service.history.start(begin, end):
                self.coverage.setText("正在分页同步历史，完成后展示实际覆盖与缺口…")
            else:
                self.coverage.setText("已有同步任务进行中，请等待当前任务完成。")
        except ValueError as exc:
            self.coverage.setText(str(exc))


class Workbench(QMainWindow):
    events_seen = pyqtSignal()

    def __init__(self, service, open_legacy, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("CoinPilot AI · 币航 — AI 加密交易工作台")
        self.resize(1200, 720)
        self.setMinimumSize(1000, 650)
        self.setStyleSheet(style_sheet())
        self.selected_event = None
        self.exiting = False
        root_widget = QWidget()
        root_widget.setObjectName("workbenchRoot")
        root = QVBoxLayout(root_widget)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)
        header = QHBoxLayout()
        brand = QLabel()
        brand.setObjectName("brandMark")
        brand.setFixedSize(36, 36)
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setPixmap(icon("brand", color("text")).pixmap(QSize(23, 23), self.devicePixelRatioF()))
        self.brand = brand
        header.addWidget(brand)
        title = QLabel("CoinPilot AI")
        title.setToolTip("币航 · AI 加密交易工作台")
        title.setObjectName("title")
        header.addWidget(title)
        self.environment_label = QLabel()
        header.addWidget(self.environment_label)
        header.addStretch()
        caption = QLabel("OKX  /  USDT PERPETUAL")
        caption.setObjectName("eyebrow")
        header.addWidget(caption)
        mini_settings = button("迷你窗口设置", open_legacy)
        set_button_icon(mini_settings, "mini")
        header.addWidget(mini_settings)
        root.addLayout(header)
        self.pages = QTabWidget()
        self.pages.setObjectName("navigation")
        self.pages.setIconSize(QSize(18, 18))
        self.pages.currentChanged.connect(self.page_changed)
        root.addWidget(self.pages, 1)
        self.monitor = self.make_monitor()
        self.pages.addTab(self.monitor, icon("monitoring"), "盯盘")
        self.trade = TradePage(service)
        self.pages.addTab(self.trade, icon("trade"), "交易")
        self.review = ReviewPage(service)
        self.pages.addTab(self.review, icon("review"), "复盘")
        self.settings_page = SettingsPage(service, open_legacy)
        self.pages.addTab(self.settings_page, icon("settings"), "设置")
        self.status = QLabel()
        self.status.setObjectName("muted")
        root.addWidget(self.status)
        self.setCentralWidget(root_widget)
        events.changed.connect(self.apply_theme)
        service.updated.connect(self.refresh)
        self.refresh("environment")
        self.refresh("market")
        self.refresh("events")
        self.refresh_watchlist()

    def make_monitor(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(3, 3, 3, 3)
        split = SavedSplitter(Qt.Orientation.Horizontal, self.service, "monitor_horizontal")
        self.monitor_vertical = SavedSplitter(Qt.Orientation.Vertical, self.service, "monitor_vertical")
        self.chart = ChartPanel(self.service)
        self.monitor_vertical.addWidget(self.chart)
        self.monitor_bottom = QTabWidget()
        events_page = QWidget()
        events_layout = QVBoxLayout(events_page)
        events_layout.setContentsMargins(2, 2, 2, 2)
        events_split = SavedSplitter(Qt.Orientation.Horizontal, self.service, "monitor_events")
        self.events = table(["时间", "事件", "合约", "级别"])
        self.events.itemSelectionChanged.connect(self.show_event)
        events_split.addWidget(self.events)
        self.event_detail = QTextBrowser()
        self.event_detail.setMinimumWidth(100)
        self.event_detail.setOpenExternalLinks(False)
        self.event_detail.setPlaceholderText("选择事件查看触发事实与 AI 解读")
        events_split.addWidget(self.event_detail)
        events_split.restore([500, 350])
        events_layout.addWidget(events_split)
        self.monitor_bottom.addTab(events_page, icon("bell"), "提醒事件")
        self.monitor_positions = table(["合约", "方向", "持仓(张)", "均价", "未实现盈亏"])
        self.monitor_bottom.addTab(self.monitor_positions, icon("layers"), "持仓摘要")
        account_page = QWidget()
        account_layout = QVBoxLayout(account_page)
        self.account_summary = QLabel("账户尚未连接")
        self.account_summary.setWordWrap(True)
        account_layout.addWidget(self.account_summary)
        account_layout.addStretch()
        self.monitor_bottom.addTab(account_page, icon("wallet"), "账户")
        self.monitor_vertical.addWidget(self.monitor_bottom)
        self.monitor_vertical.restore([430, 150])
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(2)
        center_layout.addWidget(self.monitor_vertical, 1)
        self.event_ai = AiPanel(self.service, "event", self.event_context)
        center_layout.addWidget(self.event_ai)
        split.addWidget(center)
        self.monitor_sidebar = QWidget()
        self.monitor_sidebar.setObjectName("sidePanel")
        self.monitor_sidebar.setMinimumWidth(180)
        self.monitor_sidebar.setMaximumWidth(360)
        layout = QVBoxLayout(self.monitor_sidebar)
        layout.setContentsMargins(8, 7, 5, 3)
        heading = QLabel("自选列表")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.watchlist = QListWidget()
        self.watchlist.setItemDelegate(WatchlistDelegate(self.watchlist))
        self.watchlist.itemClicked.connect(lambda item: self.service.select(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.watchlist)
        self.watch_input = QLineEdit()
        self.watch_input.setPlaceholderText("BTCUSDT")
        self.watch_input.returnPressed.connect(self.add_watch)
        layout.addWidget(self.watch_input)
        row = QHBoxLayout()
        row.addWidget(button("添加", self.add_watch))
        row.addWidget(button("移除", self.remove_watch))
        layout.addLayout(row)
        self.watch_feedback = QLabel()
        self.watch_feedback.setWordWrap(True)
        layout.addWidget(self.watch_feedback)
        self.quote_label = QLabel("等待 OKX 行情")
        self.quote_label.setWordWrap(True)
        layout.addWidget(self.quote_label)
        source_label = QLabel("行情来源  OKX · USDT 永续")
        source_label.setObjectName("muted")
        layout.addWidget(source_label)
        split.addWidget(self.monitor_sidebar)
        split.setStretchFactor(0, 1)
        split.restore([930, 240])
        self.chart.maximize_requested.connect(self.maximize_monitor)
        self.chart.bottom_requested.connect(lambda: self.toggle_monitor("bottom"))
        self.chart.sidebar_requested.connect(lambda: self.toggle_monitor("sidebar"))
        visibility = self.service.store.get("chart_layout", "monitor_visibility", {"bottom": True, "sidebar": True})
        self.monitor_bottom.setVisible(visibility["bottom"])
        self.monitor_sidebar.setVisible(visibility["sidebar"])
        root.addWidget(split)
        return page

    def maximize_monitor(self, maximized):
        if maximized:
            self.monitor_visibility = (not self.monitor_bottom.isHidden(), not self.monitor_sidebar.isHidden(), not self.event_ai.isHidden())
            self.monitor_bottom.hide()
            self.monitor_sidebar.hide()
            self.event_ai.hide()
        else:
            bottom, sidebar, ai = self.monitor_visibility
            self.monitor_bottom.setVisible(bottom)
            self.monitor_sidebar.setVisible(sidebar)
            self.event_ai.setVisible(ai)

    def toggle_monitor(self, area):
        if self.chart.maximized:
            self.chart.maximize()
        widget = self.monitor_bottom if area == "bottom" else self.monitor_sidebar
        widget.setVisible(widget.isHidden())
        self.service.store.put("chart_layout", "monitor_visibility", {"bottom": not self.monitor_bottom.isHidden(), "sidebar": not self.monitor_sidebar.isHidden()})

    def add_watch(self):
        try:
            self.service.save_watchlist(self.service.settings["watchlist"] + [self.watch_input.text()])
            self.watch_input.clear()
            self.watch_feedback.clear()
            self.refresh_watchlist()
        except ValueError as exc:
            self.watch_feedback.setText(str(exc))

    def remove_watch(self):
        item = self.watchlist.currentItem()
        if item:
            inst = item.data(Qt.ItemDataRole.UserRole)
            self.service.save_watchlist([v for v in self.service.settings["watchlist"] if v != inst])
            self.refresh_watchlist()

    def refresh_watchlist(self):
        self.watchlist.clear()
        for inst in self.service.settings["watchlist"]:
            quote = self.service.quotes.get(inst, {})
            text = f"{inst.replace('-USDT-SWAP', '')}   {quote.get('price', '—')}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, inst)
            item.setData(Qt.ItemDataRole.UserRole + 1, quote.get("price", "—"))
            item.setData(Qt.ItemDataRole.UserRole + 2, bool(quote and time.time()-quote["time"] <= 30))
            self.watchlist.addItem(item)
            if inst == self.service.selected:
                item.setSelected(True)

    def event_context(self):
        if self.selected_event:
            event = self.service.store.get("event", self.selected_event, scope=self.service.scope)
            if event:
                return dict(event["context"], events=[{k: v for k, v in event.items() if k not in ("context", "analysis")}])
        return self.service.context()

    def show_event(self):
        key = selected_id(self.events)
        if not key:
            return
        event = self.service.store.get("event", key, scope=self.service.scope)
        if not event:
            return
        self.selected_event = key
        facts = {k: v for k, v in event.items() if k not in ("context", "analysis")}
        self.event_detail.setMarkdown(f"### {event['name']}\n\n```json\n{encode(facts)}\n```\n\n" + (event.get("analysis") or "AI 尚未解读，可展开下方 AI 面板按需分析。"))
        self.events_seen.emit()

    def page_changed(self, index):
        if self.service.closed or not hasattr(self, "settings_page"):
            return
        if index == 0:
            self.events_seen.emit()
        if index in (0, 1, 2):
            (self.event_ai if index == 0 else self.trade.ai_panel if index == 1 else self.review.ai_panel).reload_templates()
        if index == 2:
            self.review.refresh()
        if index == 3:
            self.settings_page.reload()

    def apply_theme(self, _theme_id):
        self.setStyleSheet(style_sheet())
        self.brand.setPixmap(icon("brand", color("text")).pixmap(QSize(23, 23), self.devicePixelRatioF()))
        self.watchlist.viewport().update()
        self.refresh("status")

    def refresh(self, kind):
        s = self.service
        if s.closed:
            return
        self.environment_label.setText("模拟环境" if s.environment == "demo" else "真实环境")
        badge_color = color("warning" if s.environment == "demo" else "negative")
        self.environment_label.setStyleSheet(
            f"padding:4px 10px;border:1px solid {badge_color};border-radius:7px;"
            f"font-size:11px;color:{badge_color};background:{color('surface_raised')};font-weight:600;")
        if kind in ("price", "selection", "environment", "status"):
            q = s.quotes.get(s.selected)
            fresh = q and time.time() - q["time"] <= 30
            self.quote_label.setText(s.selected + "    " + (q["price"] if q else "—") + ("" if fresh else " · 数据未就绪／过期"))
            if kind != "status":
                self.refresh_watchlist()
        if kind in ("candles", "selection", "environment"):
            self.chart.set_data(s.candles.get((s.selected, s.bar), []), s.selected + " / " + s.bar)
        if kind in ("events", "environment"):
            self.events.blockSignals(True)
            fill_table(self.events, [(key, [timestamp(e["time"]), e["name"], e.get("instrument", ""), {"market": "行情", "position": "持仓", "order": "订单"}.get(e.get("priority"), "行情")]) for key, e in s.store.list("event", s.scope, limit=200)])
            self.events.blockSignals(False)
            if kind == "events":
                self.show_event()
        if kind in ("account", "environment"):
            self.account_summary.setText("账户权益 " + str(s.balance.get("totalEq", "—")) + " USD\n持仓 " + str(len(s.positions)) + " 项\n" + (s.account_error or "账户同步正常"))
            self.settings_page.update_status()
            fill_table(self.monitor_positions, [(p.get("posId"), [p["instId"], p["posSide"], p["pos"], p.get("avgPx", "—"), p.get("upl", "—")]) for p in s.positions])
        if kind == "environment":
            self.selected_event = None
            self.event_detail.clear()
            for panel in (self.event_ai, self.trade.ai_panel, self.review.ai_panel):
                panel.last_record = None
                panel.last_request = None
                panel.answer.clear()
            self.settings_page.reload_rules()
            self.review.refresh()
        self.trade.refresh(kind)
        if kind == "history":
            self.review.refresh()
        if kind == "reports":
            self.review.refresh_reports()
        stale = time.time() - s.account.get("time", 0) > 20
        self.status.setText("OKX 行情：" + (s.market_error or "运行中") + "  |  账户：" + (s.account_error or ("数据过期" if stale else "已同步")) + "  |  " + ("通知已暂停" if s.settings.get("notifications_paused") else "通知开启") + "  |  关闭窗口后继续后台运行")

    def closeEvent(self, event):
        if self.exiting:
            event.accept()
            return
        event.ignore()
        self.hide()
