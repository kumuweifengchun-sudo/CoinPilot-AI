"""桌面工作台主窗口。关闭仅隐藏，后台服务由应用持有。"""
import time

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from coinpilot_ai.charts.panel import ChartPanel, SavedSplitter
from coinpilot_ai.charts.multi import MultiChart
from coinpilot_ai.research.scanner_panel import ScannerPanel
from coinpilot_ai.research.replay_panel import ReplayPage
from coinpilot_ai.market.panel import MicroPanel
from coinpilot_ai.research.strategy_panel import StrategyPage
from coinpilot_ai.core.store import encode
from coinpilot_ai.review.page import ReviewPage
from coinpilot_ai.ui.common import button, fill_table, selected_id, table, timestamp
from coinpilot_ai.workbench.settings import SettingsPage
from coinpilot_ai.trading.dialogs import RuleDialog
from coinpilot_ai.trading.models import OrderDraft, number
from coinpilot_ai.trading.panel import TradeController, RiskDialog
from .workspace import WorkspaceLayout
from .profiles import WorkspaceProfiles
from .titlebar import WorkbenchTitleBar
from coinpilot_ai.ui.icons import icon
from coinpilot_ai.ui.theme import events, style_sheet
from .watchlist import WatchlistDelegate


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
        self.workspace.profiles = WorkspaceProfiles(service, self.workspace, self.multi_chart, self.scanner_panel)
        self.title_bar.bind_workspace(self.workspace)
        self.pages.addTab(self.workspace.host, icon("trade"), "工作台")
        self.chart.maximize_requested.connect(self.workspace.maximize)
        self.chart.bottom_requested.connect(lambda: self.workspace.toggle("info"))
        self.chart.sidebar_requested.connect(lambda: self.monitor_sidebar.setVisible(self.monitor_sidebar.isHidden()))
        self.trade.form_requested.connect(self.show_order_form)
        self.chart.canvas.order_requested.connect(self.chart_order)
        self.chart.canvas.price_alert_requested.connect(self.chart_alert)
        self.chart.canvas.risk_requested.connect(self.chart_risk)
        self.multi_chart.risk_requested.connect(self.chart_risk)
        self.multi_chart.alert_requested.connect(self.chart_alert)
        self.multi_chart.order_requested.connect(self.chart_order)
        for dock in self.workspace.docks.values():
            dock.visibilityChanged.connect(self.sync_visible)
        self.review = ReviewPage(service)
        self.review_tabs = QTabWidget()
        self.review_tabs.addTab(self.review, "交易复盘")
        self.replay = ReplayPage(service)
        self.review_tabs.addTab(self.replay, "Bar Replay · 训练")
        self.strategy_page = StrategyPage(service)
        self.review_tabs.addTab(self.strategy_page, "策略与回测")
        self.review_tabs.currentChanged.connect(lambda index: self.replay.pause() if index != 1 else None)
        self.pages.addTab(self.review_tabs, icon("review"), "复盘")
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
        self.multi_chart = MultiChart(self.service, self.chart)
        split.addWidget(self.multi_chart)
        self.monitor_sidebar = QWidget()
        self.monitor_sidebar.setObjectName("sidePanel")
        self.monitor_sidebar.setMinimumWidth(180)
        self.monitor_sidebar.setMaximumWidth(360)
        layout = QVBoxLayout(self.monitor_sidebar)
        layout.setContentsMargins(8, 7, 5, 3)
        tabs = QTabWidget()
        watch_page = QWidget()
        watch_layout = QVBoxLayout(watch_page)
        watch_layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("自选列表")
        heading.setObjectName("sectionTitle")
        watch_layout.addWidget(heading)
        self.watchlist = QListWidget()
        self.watchlist.setItemDelegate(WatchlistDelegate(self.watchlist))
        self.watchlist.itemClicked.connect(lambda item: self.service.select(item.data(Qt.ItemDataRole.UserRole)))
        watch_layout.addWidget(self.watchlist)
        self.watch_input = QLineEdit()
        self.watch_input.setPlaceholderText("BTCUSDT")
        self.watch_input.returnPressed.connect(self.add_watch)
        watch_layout.addWidget(self.watch_input)
        row = QHBoxLayout()
        row.addWidget(button("添加", self.add_watch))
        row.addWidget(button("移除", self.remove_watch))
        watch_layout.addLayout(row)
        self.watch_feedback = QLabel()
        self.watch_feedback.setWordWrap(True)
        watch_layout.addWidget(self.watch_feedback)
        self.quote_label = QLabel("等待 OKX 行情")
        self.quote_label.setWordWrap(True)
        watch_layout.addWidget(self.quote_label)
        source_label = QLabel("行情来源  OKX · USDT 永续")
        source_label.setObjectName("muted")
        watch_layout.addWidget(source_label)
        tabs.addTab(watch_page, "自选")
        self.scanner_panel = ScannerPanel(self.service)
        tabs.addTab(self.scanner_panel, "扫描")
        self.micro_panel = MicroPanel(self.service, self.chart)
        tabs.addTab(self.micro_panel, "盘口")
        layout.addWidget(tabs)
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
        account_page = QWidget()
        account_layout = QVBoxLayout(account_page)
        account_layout.addWidget(self.account_summary)
        account_layout.addWidget(self.trade.equity_curve)
        self.trade.tabs.addTab(account_page, "账户")

    def chart_order(self, environment, instrument, price, direction):
        if self.service.closed or environment != self.service.environment:
            return
        self.trade.load_draft(OrderDraft(instrument, direction=direction, order_type='limit', price=price,
                                        margin=self.trade.margin.currentData()))
        self.trade.set_feedback(f'{self.service.environment_label} · 已填入限价 {price}，请填写张数并检查确认。')

    def chart_risk(self, environment, instrument, price):
        if self.service.closed or environment != self.service.environment:
            return
        dialog = RiskDialog(self.service, instrument, self.trade.apply_risk_plan, self)
        dialog.fields["entry"].setText(price)
        dialog.exec()

    def chart_alert(self, environment, instrument, price):
        service = self.service
        if service.closed or environment != service.environment:
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
        if index != 1:
            self.replay.pause()
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
            self.replay.shutdown()
            self.strategy_page.shutdown()
            self.workspace.shutdown()
            event.accept()
            return
        event.ignore()
        self.hide()
