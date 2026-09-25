"""桌面工作台主窗口。关闭仅隐藏，后台服务由应用持有。"""
import time

from PyQt6.QtCore import QDateTime, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QDateTimeEdit, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from .chart_panel import ChartPanel, SavedSplitter
from .store import encode
from .ui_ai import AiPanel
from .ui_common import button, fill_table, selected_id, show_text, table, timestamp
from .ui_settings import SettingsPage, RuleDialog
from .domain import OrderDraft, number
from .ui_trade import TradeController
from .workspace import WorkspaceLayout
from .titlebar import WorkbenchTitleBar
from ..icons import icon
from ..theme import events, style_sheet
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
            if not self.service.account_connected:
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

    def __init__(self, service, open_legacy=None, parent=None, *, settings_owner=None, updater=None):
        super().__init__(parent)
        self.service = service
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        area = self.screen().availableGeometry()
        self.resize(min(1440, area.width()), min(900, area.height()))
        self.setMinimumSize(min(1000, area.width()), min(650, area.height()))
        self.setStyleSheet(style_sheet())
        self.selected_event = None
        self.exiting = False
        root_widget = QWidget()
        root_widget.setObjectName("workbenchRoot")
        root = QVBoxLayout(root_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.pages = QTabWidget()
        self.pages.setObjectName("navigation")
        self.pages.setIconSize(QSize(18, 18))
        self.pages.tabBar().hide()
        self.pages.currentChanged.connect(self.page_changed)
        self.title_bar = WorkbenchTitleBar(self, self.pages)
        root.addWidget(self.title_bar)
        body = QVBoxLayout()
        body.setContentsMargins(8, 0, 8, 6)
        body.setSpacing(4)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        self.trade = TradeController(service, self)
        self.chart = ChartPanel(service, trading=True)
        self.ai_placeholder = QLabel("AI 功能规划中")
        self.ai_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ai_placeholder.setMinimumSize(140, 100)
        self.ai_placeholder.setObjectName("muted")
        market = self.make_market()
        self.make_information()
        self.workspace = WorkspaceLayout(self, service, {
            "ai": ("AI", self.ai_placeholder), "market": ("盘面", market),
            "info": ("账户与交易信息", self.trade.bottom), "order": ("委托下单", self.trade.sidebar)})
        self.title_bar.bind_workspace(self.workspace)
        self.pages.addTab(self.workspace.host, icon("trade"), "工作台")
        self.chart.maximize_requested.connect(self.workspace.maximize)
        self.chart.bottom_requested.connect(lambda: self.workspace.toggle("info"))
        self.chart.sidebar_requested.connect(lambda: self.monitor_sidebar.setVisible(self.monitor_sidebar.isHidden()))
        self.trade.form_requested.connect(self.show_order_form)
        self.chart.canvas.order_requested.connect(self.chart_order)
        self.chart.canvas.price_alert_requested.connect(self.chart_alert)
        for dock in self.workspace.docks.values():
            dock.visibilityChanged.connect(self.sync_visible)
        self.review = ReviewPage(service)
        self.pages.addTab(self.review, icon("review"), "复盘")
        self.settings_page = SettingsPage(service, parent=self, owner=settings_owner, updater=updater, workspace=self.workspace)
        self.chart.preferences_requested.connect(lambda: self.open_settings("图表"))
        self.pages.addTab(self.settings_page, icon("settings"), "设置")
        self.status = QLabel()
        self.status.setObjectName("muted")
        body.addWidget(self.status)
        self.setCentralWidget(root_widget)
        events.changed.connect(self.apply_theme)
        service.updated.connect(self.refresh)
        self.refresh("environment")
        self.refresh("market")
        self.refresh("events")
        self.refresh_watchlist()

    def make_market(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        split = SavedSplitter(Qt.Orientation.Horizontal, self.service, "workspace_market")
        split.addWidget(self.chart)
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
        split.restore([740, 180])
        self.monitor_sidebar.hide()
        root.addWidget(split)
        return page

    def make_information(self):
        self.events = table(["时间", "事件", "合约", "级别"], readable=True)
        self.events.itemSelectionChanged.connect(self.show_event)
        self.event_detail = QTextBrowser()
        self.event_detail.setOpenExternalLinks(False)
        self.event_detail.setPlaceholderText("选择事件查看触发事实")
        split = SavedSplitter(Qt.Orientation.Horizontal, self.service, "workspace_events")
        split.addWidget(self.events)
        split.addWidget(self.event_detail)
        split.restore([500, 350])
        self.trade.tabs.addTab(split, "提醒事件")
        self.account_summary = QLabel("账户尚未连接")
        self.account_summary.setWordWrap(True)
        self.account_summary.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.trade.tabs.addTab(self.account_summary, "账户")

    def chart_order(self, environment, instrument, price, direction):
        if self.service.closed or environment != self.service.environment or instrument != self.service.selected:
            return
        self.trade.load_draft(OrderDraft(instrument, direction=direction, order_type='limit', price=price,
                                        margin=self.trade.margin.currentData()))
        self.trade.set_feedback(f'{self.service.environment_label} · 已填入限价 {price}，请填写张数并检查确认。')

    def chart_alert(self, environment, instrument, price):
        service = self.service
        if service.closed or environment != service.environment or instrument != service.selected:
            return
        generation = service.generation
        current = service.quotes.get(instrument, {}).get('price')
        op = 'below' if current and number(price) < number(current) else 'above'
        rule = {'name': f'{instrument} 价格 {"≤" if op == "below" else "≥"} {price}',
                'instrument': instrument, 'mode': 'all', 'cooldown': 300,
                'conditions': [{'metric': 'price', 'op': op, 'threshold': price, 'window': 900}]}
        dialog = RuleDialog(service, rule, self.chart.canvas.window())
        dialog.setWindowTitle('价格提醒 · ' + service.environment_label)
        if dialog.exec() and not service.closed and generation == service.generation:
            service.save_rule(dialog.rule)
            self.settings_page.reload_rules()
        dialog.deleteLater()

    def show_order_form(self):
        self.pages.setCurrentIndex(0)
        self.workspace.set_visible("order", True)
        self.trade.size.setFocus()

    def sync_visible(self, visible):
        if visible and hasattr(self, "settings_page") and not self.service.closed:
            for kind in ("account", "orders", "events", "market"):
                self.refresh(kind)

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
        self.event_detail.setMarkdown(f"### {event['name']}\n\n```json\n{encode(facts)}\n```\n\n")
        self.events_seen.emit()

    def page_changed(self, index):
        if self.service.closed or not hasattr(self, "settings_page"):
            return
        self.workspace.activate(index == 0 and self.isVisible())
        if index == 0:
            self.events_seen.emit()
            self.sync_visible(True)
        elif index == 1:
            self.review.ai_panel.reload_templates()
            self.review.refresh()
        elif index == 2:
            self.settings_page.refresh_status()

    def open_settings(self, section="常规与网络"):
        self.pages.setCurrentWidget(self.settings_page)
        self.settings_page.select_section(section)
        self.settings_page.refresh_status()

    def showEvent(self, event):
        super().showEvent(event)
        self.title_bar.setup_native_frame()
        if hasattr(self, "workspace"):
            self.workspace.activate(self.pages.currentIndex() == 0)
            self.sync_visible(True)

    def hideEvent(self, event):
        if hasattr(self, "workspace"):
            self.workspace.activate(False)
        super().hideEvent(event)

    def apply_theme(self, _theme_id):
        self.setStyleSheet(style_sheet())
        self.title_bar.apply_theme()
        self.watchlist.viewport().update()
        self.refresh("status")

    def nativeEvent(self, event_type, message):
        if hasattr(self, 'title_bar'):
            result = self.title_bar.native_event(message)
            if result is not None:
                return result
        # 未处理的消息交回 Qt；不调用 SIP 的基类 nativeEvent 指针桥接。
        return False, 0

    def refresh(self, kind):
        s = self.service
        if s.closed:
            return
        environment = s.environment_label
        title = f"CoinPilot AI · {environment}"
        if self.windowTitle() != title:
            self.setWindowTitle(title)
        if kind in ("selection", "environment", "market"):
            for key, title in (("market", "盘面"), ("order", "委托下单")):
                self.workspace.docks[key].setWindowTitle(f"{title} · {s.selected} · {environment}")
        if kind in ("price", "selection", "environment", "status"):
            q = s.quotes.get(s.selected)
            fresh = q and time.time() - q["time"] <= 30
            self.quote_label.setText(s.selected + "    " + (q["price"] if q else "—") + ("" if fresh else " · 数据未就绪／过期"))
            if kind != "status":
                self.refresh_watchlist()
        if kind == "environment" or (kind == "events" and self.trade.bottom.isVisible()):
            self.events.blockSignals(True)
            fill_table(self.events, [(key, [timestamp(e["time"]), e["name"], e.get("instrument", ""), {"market": "行情", "position": "持仓", "order": "订单"}.get(e.get("priority"), "行情")]) for key, e in s.store.list("event", s.scope, limit=200)])
            self.events.blockSignals(False)
            if kind == "events":
                self.show_event()
        if kind in ("account", "environment"):
            self.account_summary.setText(environment + "\n账户权益 " + str(s.balance.get("totalEq", "—")) + " USDT\n可用资金 " + str(s.balance.get('availEq', '—')) + " USDT\n持仓 " + str(len(s.positions)) + " 项\n" + (s.account_error or "账户同步正常"))
            self.settings_page.update_status()
        if kind == "environment":
            self.selected_event = None
            self.event_detail.clear()
            for panel in (self.review.ai_panel,):
                panel.last_record = None
                panel.last_request = None
                panel.answer.clear()
            self.settings_page.reload_rules()
            self.review.refresh()
        if kind in ("selection", "environment") or self.trade.sidebar.isVisible() or self.trade.bottom.isVisible():
            self.trade.refresh(kind)
        if kind == "history":
            self.review.refresh()
        if kind == "reports":
            self.review.refresh_reports()
        stale = time.time() - s.account.get("time", 0) > 20
        self.status.setText("OKX 行情：" + (s.market_error or "运行中") + "  |  账户：" + (s.account_error or ("数据过期" if stale else "已同步")) + "  |  " + ("通知已暂停" if s.settings.get("notifications_paused") else "通知开启") + "  |  关闭窗口后继续后台运行")

    def closeEvent(self, event):
        if self.exiting:
            self.workspace.shutdown()
            event.accept()
            return
        event.ignore()
        self.hide()
