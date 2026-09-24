"""两个页面共用的图表工具栏、状态栏与画布。"""
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSplitter,
                            QToolButton, QVBoxLayout, QWidget)

from .chart import CandleChart
from .chart_dialogs import EmaDialog, ObjectsDialog, edit_drawing
from .chart_state import BARS, TOOLS, trade_lines
from ..icons import icon, set_button_icon
from ..theme import chart_controls_style, events


class SavedSplitter(QSplitter):
    def __init__(self, orientation, service, name, parent=None):
        super().__init__(orientation, parent)
        self.service, self.name = service, name
        self.setHandleWidth(4)
        self.splitterMoved.connect(self.save)

    def restore(self, sizes):
        saved = self.service.store.get("chart_layout", self.name, sizes)
        if len(saved) == self.count() and sum(saved) > 0:
            self.setSizes(saved)
        else:
            self.setSizes(sizes)

    def save(self):
        if not self.service.closed:
            self.service.store.put("chart_layout", self.name, self.sizes())


class ChartPanel(QWidget):
    maximize_requested = pyqtSignal(bool)
    bottom_requested = pyqtSignal()
    sidebar_requested = pyqtSignal()

    def __init__(self, service, *, trading=False, parent=None):
        super().__init__(parent)
        self.service, self.trading = service, trading
        self.maximized = False
        self.warm_pending = False
        self.range_timer = QTimer(self)
        self.range_timer.setSingleShot(True)
        self.range_timer.setInterval(300)
        self.range_timer.timeout.connect(self.ensure_view)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        controls = QHBoxLayout()
        controls.setSpacing(2)
        self.period_buttons = {}
        for bar in BARS:
            btn = self.small_button(bar, lambda _, b=bar: self.select_bar(b))
            btn.setCheckable(True)
            self.period_buttons[bar] = btn
            controls.addWidget(btn)
        controls.addStretch()
        tools_menu = QMenu(self)
        for key, name in TOOLS.items():
            action = tools_menu.addAction(name, lambda checked=False, k=key: self.choose_tool(k))
            action.setIcon(icon(key))
        tools_button = self.small_button("绘图", lambda: None)
        tools_button.setMenu(tools_menu)
        controls.addWidget(tools_button)
        controls.addWidget(self.small_button("EMA", lambda: self.ema_settings()))
        controls.addWidget(self.small_button("对象", lambda: ObjectsDialog(self.canvas, self).exec()))
        self.auto_button = self.small_button("自动", lambda: self.canvas.auto())
        self.auto_button.setCheckable(True)
        self.auto_button.setToolTip("自动缩放价格；也可双击右侧价格轴")
        controls.addWidget(self.auto_button)
        controls.addWidget(self.small_button("最新", lambda: self.canvas.latest()))
        self.max_button = self.small_button("放大", self.maximize)
        controls.addWidget(self.max_button)
        root.addLayout(controls)
        row = QHBoxLayout()
        row.setSpacing(0)
        toolbar = QWidget()
        tools = QVBoxLayout(toolbar)
        tools.setContentsMargins(2, 2, 3, 2)
        tools.setSpacing(1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.tool_buttons = {}
        for key, name in TOOLS.items():
            btn = QToolButton()
            btn.setToolTip(name)
            set_button_icon(btn, key, size=18)
            btn.setCheckable(True)
            btn.setFixedSize(32, 27)
            btn.clicked.connect(lambda _, k=key: self.canvas.set_tool(k))
            self.group.addButton(btn)
            self.tool_buttons[key] = btn
            tools.addWidget(btn)
        self.tool_buttons["cursor"].setChecked(True)
        self.magnet_button = QToolButton()
        self.magnet_button.setToolTip("K 线磁吸：锚点靠近最高价／最低价时吸附；按住 Alt 临时关闭")
        self.magnet_button.setCheckable(True)
        self.magnet_button.setChecked(service.chart_book.magnet_enabled)
        self.magnet_button.setFixedSize(32, 27)
        set_button_icon(self.magnet_button, "magnet", size=18)
        self.magnet_button.toggled.connect(lambda enabled: self.canvas.set_magnet(enabled))
        tools.addSpacing(5)
        tools.addWidget(self.magnet_button)
        for name, tip, callback in (("undo-2", "撤销 Ctrl+Z", lambda: self.undo(False)), ("redo-2", "重做 Ctrl+Shift+Z", lambda: self.undo(True))):
            btn = QToolButton()
            btn.setToolTip(tip)
            set_button_icon(btn, name, size=17)
            btn.setFixedSize(32, 25)
            btn.clicked.connect(callback)
            tools.addWidget(btn)
        tools.addStretch()
        tools_scroll = QScrollArea()
        tools_scroll.setWidgetResizable(True)
        tools_scroll.setWidget(toolbar)
        tools_scroll.setFixedWidth(40)
        tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tools_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tools_scroll.setMinimumHeight(90)
        row.addWidget(tools_scroll)
        self.canvas = CandleChart(service)
        self.canvas.magnet_changed.connect(self.sync_magnet)
        # 左侧工具栏在紧凑窗口里可滚动；顶部绘图菜单始终提供相同开关。
        tools_menu.addSeparator()
        self.magnet_action = tools_menu.addAction("K 线高低点磁吸")
        self.magnet_action.setIcon(icon("magnet"))
        self.magnet_action.setCheckable(True)
        self.magnet_action.setChecked(self.canvas.magnet_enabled)
        self.magnet_action.toggled.connect(self.canvas.set_magnet)
        self.canvas.history_requested.connect(self.load_older)
        self.canvas.settings_requested.connect(self.ema_settings)
        self.canvas.edit_requested.connect(lambda identity: edit_drawing(self.canvas, identity, self))
        self.canvas.tool_finished.connect(lambda: self.tool_buttons["cursor"].setChecked(True))
        self.canvas.view_changed.connect(lambda: self.auto_button.setChecked(self.canvas.auto_scale))
        self.canvas.view_changed.connect(self.range_timer.start)
        row.addWidget(self.canvas, 1)
        root.addLayout(row, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(3)
        self.status = QLabel()
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        self.retry_button = self.small_button("历史 / 重试", self.retry_history)
        bottom.addWidget(self.retry_button)
        bottom.addWidget(self.small_button("底栏", self.bottom_requested.emit))
        bottom.addWidget(self.small_button("侧栏", self.sidebar_requested.emit))
        root.addLayout(bottom)
        self.setStyleSheet(chart_controls_style())
        events.changed.connect(self.apply_theme)
        service.updated.connect(self.refresh)
        service.chart_book.changed.connect(self.settings_changed)
        self.refresh("selection")

    def apply_theme(self, _theme_id):
        self.setStyleSheet(chart_controls_style())

    def small_button(self, text, callback):
        btn = QPushButton(text)
        btn.setObjectName("chartButton")
        btn.setToolTip(text)
        names = {"绘图": "pencil", "对象": "layers", "放大": "maximize", "底栏": "panel-bottom", "侧栏": "panel-right"}
        if text in names:
            # 常用图表动作仅显示矢量图标，将横向空间留给周期与图表。
            set_button_icon(btn, names[text])
            btn.setText("")
            btn.setFixedWidth(28)
        btn.clicked.connect(callback)
        return btn

    def sync_magnet(self, enabled):
        for control in (self.magnet_button, self.magnet_action):
            control.blockSignals(True)
            control.setChecked(enabled)
            control.blockSignals(False)

    def select_bar(self, bar):
        self.canvas.flush_view()
        self.service.select(self.service.selected, bar)

    def choose_tool(self, tool):
        self.tool_buttons[tool].setChecked(True)
        self.canvas.set_tool(tool)

    def maximize(self):
        self.maximized = not self.maximized
        self.max_button.setIcon(icon("minimize" if self.maximized else "maximize"))
        self.max_button.setToolTip("还原图表" if self.maximized else "放大图表")
        self.maximize_requested.emit(self.maximized)

    def undo(self, redo):
        self.service.chart_book.undo(self.canvas.environment, self.canvas.instrument, redo)
        self.canvas.setFocus()

    def ema_settings(self):
        EmaDialog(self.service.chart_book, self).exec()

    def settings_changed(self, kind, key):
        if kind == "ema":
            self.maybe_warm()

    def load_older(self):
        if not self.service.closed:
            pair = (self.service.selected, self.service.bar)
            if pair in self.service.chart_feed.repairs:
                self.service.chart_feed.repair(pair)
                return
            self.service.chart_feed.fetch(self.service.selected, self.service.bar, older=True)

    def ensure_view(self):
        if self.service.running and not self.service.closed and not self.canvas.follow:
            self.service.chart_feed.ensure_range((self.canvas.instrument, self.canvas.bar), self.canvas.left_time, self.canvas.count)

    def retry_history(self):
        self.service.chart_feed.window_attempts.clear()
        self.ensure_view()
        self.load_older()

    def maybe_warm(self):
        if not self.service.running or self.warm_pending:
            return
        pair = (self.service.selected, self.service.bar)
        rows = self.service.candles.get(pair, [])
        need = max((e["period"]*5 for e in self.service.chart_book.emas if e["visible"]), default=0)
        if rows and len(rows) < need and not self.service.chart_feed.history_state.get(pair):
            self.warm_pending = True
            QTimer.singleShot(350, self.warm_next)

    def warm_next(self):
        self.warm_pending = False
        if not self.service.closed:
            pair = (self.service.selected, self.service.bar)
            need = max((e["period"]*5 for e in self.service.chart_book.emas if e["visible"]), default=0)
            if len(self.service.candles.get(pair, [])) < need:
                self.load_older()

    def set_data(self, rows, title=None):
        self.canvas.set_data(rows)

    def refresh(self, kind):
        s = self.service
        if s.closed:
            return
        if kind in ("selection", "environment"):
            self.canvas.set_context(s.environment, s.selected, s.bar)
            for bar, btn in self.period_buttons.items():
                btn.setChecked(bar == s.bar)
            self.tool_buttons["cursor"].setChecked(True)
            self.canvas.tool = "cursor"
        if kind in ("candles", "selection", "environment"):
            self.canvas.set_data(s.candles.get((s.selected, s.bar), []))
            self.maybe_warm()
            if not self.range_timer.isActive():
                self.range_timer.start()
        if kind in ("price", "status", "selection", "environment"):
            quote = s.quotes.get(s.selected, {})
            self.canvas.last_price = float(quote["price"]) if quote else None
            self.canvas.price_stale = time.time()-quote.get("time", 0) > 30
        if kind in ("account", "orders", "status", "selection", "environment"):
            self.canvas.overlays = trade_lines(s) if self.trading else []
            self.canvas.account_stale = time.time()-s.account.get("time", 0) > 20 or bool(s.account_error)
        self.status.setText("OKX · " + s.chart_feed.text((s.selected, s.bar)))
        self.status.setToolTip(self.status.text()+"\n空白拖动平移 · 滚轮缩放 · 拖动右侧价格轴拉伸 · 点击 EMA 图例编辑")
        self.auto_button.setChecked(self.canvas.auto_scale)
        self.canvas.update()
