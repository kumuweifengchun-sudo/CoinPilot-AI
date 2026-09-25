"""两个页面共用的图表工具栏、状态栏与画布。"""
import time
from copy import deepcopy
from dataclasses import replace

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy, QSplitter,
                            QToolButton, QVBoxLayout, QWidget)

from .canvas import CandleChart
from .dialogs import EmaDialog, ObjectsDialog, edit_drawing
from .settings import IndicatorSettingsDialog
from .indicator_strip import IndicatorStrip
from .derivative_strip import DerivativeStrip
from .instrument_selector import InstrumentSelector
from coinpilot_ai.market.intervals import BARS
from coinpilot_ai.charts.state import TOOLS, trade_lines, normalize_indicators
from coinpilot_ai.trading.models import instrument_id
from coinpilot_ai.ui.icons import icon, set_button_icon
from coinpilot_ai.ui.theme import chart_controls_style, events


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


class PaneIndicators:
    """单个图窗的指标配置，编辑接口与共享 ChartBook 保持一致。"""
    def __init__(self, service, key, changed):
        self.service, self.key, self.changed = service, key, changed
        record = service.store.get("chart_indicators", key)
        self.indicators = deepcopy(record["items"] if record and record.get("version") == 1
                                   else service.chart_book.indicators)

    def save_indicators(self, items):
        self.indicators = normalize_indicators(items)
        self.service.store.put("chart_indicators", self.key, {"version": 1,
                                                                "items": deepcopy(self.indicators)})
        self.changed()


class ChartPanel(QWidget):
    maximize_requested = pyqtSignal(bool)
    bottom_requested = pyqtSignal()
    sidebar_requested = pyqtSignal()
    preferences_requested = pyqtSignal()
    context_changed = pyqtSignal(str, str)

    def __init__(self, service, *, trading=False, local_pair=None, local_key=None, parent=None):
        super().__init__(parent)
        self.service, self.trading = service, trading
        self.local_pair = local_pair
        self.local_key = local_key
        self.indicator_book = service.chart_book
        self.maximized = False
        self.warm_pending = False
        self.pending_kinds = set()
        self.pending_change = None
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(33)
        self.refresh_timer.timeout.connect(self.flush_refresh)
        self.range_timer = QTimer(self)
        self.range_timer.setSingleShot(True)
        self.range_timer.setInterval(300)
        self.range_timer.timeout.connect(self.ensure_view)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        selectors = QHBoxLayout()
        selectors.setSpacing(6)
        self.symbol_choice = InstrumentSelector(service, self.instrument, self)
        self.symbol_choice.instrument_selected.connect(self.select_symbol)
        selectors.addWidget(self.symbol_choice)
        root.addLayout(selectors)
        controls = QHBoxLayout()
        controls.setSpacing(2)
        self.period_buttons = {}
        for bar in BARS:
            btn = self.small_button(bar, lambda _, b=bar: self.select_bar(b))
            btn.setCheckable(True)
            self.period_buttons[bar] = btn
            controls.addWidget(btn)
        self.compact_bar = QComboBox()
        for bar in BARS:
            self.compact_bar.addItem(bar.replace('m', 'min'), bar)
        self.compact_bar.setAccessibleName('K 线周期')
        self.compact_bar.setMinimumContentsLength(5)
        self.compact_bar.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.compact_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.compact_bar.activated.connect(lambda _index: self.select_bar(self.compact_bar.currentData()))
        selectors.addWidget(self.compact_bar)
        self.compact_bar.hide()
        controls.addStretch()
        self.compact_hidden = []
        tools_menu = QMenu(self)
        for key, name in TOOLS.items():
            action = tools_menu.addAction(name, lambda checked=False, k=key: self.choose_tool(k))
            action.setIcon(icon(key))
        tools_button = self.small_button("绘图", lambda: None)
        tools_button.setMenu(tools_menu)
        controls.addWidget(tools_button)
        ema_button = self.small_button("EMA", lambda: self.ema_settings())
        controls.addWidget(ema_button)
        self.compact_hidden.append(ema_button)
        controls.addWidget(self.small_button("指标", lambda: IndicatorSettingsDialog(self.indicator_book, self).exec()))
        self.derivative_button = self.small_button("OI/Funding", self.toggle_derivatives)
        self.derivative_button.setCheckable(True)
        controls.addWidget(self.derivative_button)
        self.compact_hidden.append(self.derivative_button)
        objects_button = self.small_button("对象", lambda: ObjectsDialog(self.canvas, self).exec())
        controls.addWidget(objects_button)
        self.compact_hidden.append(objects_button)
        self.auto_button = self.small_button("自动", lambda: self.canvas.auto())
        self.auto_button.setCheckable(True)
        self.auto_button.setToolTip("自动缩放价格；也可双击右侧价格轴")
        controls.addWidget(self.auto_button)
        self.compact_hidden.append(self.auto_button)
        controls.addWidget(self.small_button("最新", lambda: self.canvas.latest()))
        self.max_button = self.small_button("放大", self.maximize)
        controls.addWidget(self.max_button)
        self.compact_hidden.append(self.max_button)
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
        if local_pair is not None:
            self.canvas.view_key = local_key or "auxiliary"
            self.indicator_book = PaneIndicators(service, local_key or "auxiliary",
                                                 self.local_indicators_changed)
            self.canvas.indicators_override = self.indicator_book.indicators
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
        self.canvas.view_changed.connect(self.schedule_range)
        row.addWidget(self.canvas, 1)
        root.addLayout(row, 1)
        self.indicator_scroll = QScrollArea()
        self.indicator_scroll.setWidgetResizable(True)
        self.indicator_scroll.setMaximumHeight(225)
        self.indicator_host = QWidget()
        self.indicator_layout = QVBoxLayout(self.indicator_host)
        self.indicator_layout.setContentsMargins(0, 0, 0, 0)
        self.indicator_layout.setSpacing(2)
        self.indicator_scroll.setWidget(self.indicator_host)
        root.addWidget(self.indicator_scroll)
        self.derivative_scroll = QScrollArea()
        self.derivative_scroll.setWidgetResizable(True)
        self.derivative_scroll.setMaximumHeight(180)
        self.derivative_host = QWidget()
        derivative_layout = QVBoxLayout(self.derivative_host)
        derivative_layout.setContentsMargins(0, 0, 0, 0)
        derivative_layout.setSpacing(1)
        self.derivative_strips = [DerivativeStrip(self, kind, self.derivative_host)
                                  for kind in ("funding", "oi", "mark", "index", "basis")]
        for strip in self.derivative_strips:
            derivative_layout.addWidget(strip)
        self.derivative_scroll.setWidget(self.derivative_host)
        self.derivative_scroll.setVisible(False)
        root.addWidget(self.derivative_scroll)
        self.indicator_strips = []
        self.rebuild_indicator_strips()
        self.canvas.view_changed.connect(self.update_indicator_strips)
        bottom = QHBoxLayout()
        bottom.setSpacing(3)
        self.status = QLabel()
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        self.retry_button = self.small_button("历史 / 重试", self.retry_history)
        bottom.addWidget(self.retry_button)
        self.bottom_button = self.small_button("底栏", self.bottom_requested.emit)
        self.sidebar_button = self.small_button("侧栏", self.sidebar_requested.emit)
        bottom.addWidget(self.bottom_button)
        bottom.addWidget(self.sidebar_button)
        root.addLayout(bottom)
        self.setStyleSheet(chart_controls_style())
        events.changed.connect(self.apply_theme)
        service.chart_feed.series_changed.connect(self.series_changed)
        service.updated.connect(self.refresh)
        service.chart_book.changed.connect(self.settings_changed)
        service.derivatives.changed.connect(self.derivatives_changed)
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
        if self.local_pair is None:
            self.service.select(self.service.selected, bar)
        else:
            self.set_local_pair(self.instrument, bar)

    @property
    def instrument(self):
        return self.local_pair[0] if self.local_pair else self.service.selected

    @property
    def bar(self):
        return self.local_pair[1] if self.local_pair else self.service.bar

    @property
    def pair(self):
        return self.instrument, self.bar

    def select_symbol(self, instrument):
        if self.local_pair is None:
            self.canvas.flush_view()
            self.service.select(instrument, self.bar)
        else:
            self.set_local_pair(instrument, self.bar)

    def set_local_pair(self, instrument, bar):
        if self.local_pair is None or bar not in BARS:
            return
        pair = (instrument_id(instrument), bar)
        if pair == self.local_pair:
            return
        self.canvas.flush_view()
        self.local_pair = pair
        self.symbol_choice.set_instrument(instrument)
        self.refresh("selection")
        self.context_changed.emit(*pair)

    def set_compact(self, compact):
        self.compact_bar.setVisible(compact)
        self.compact_bar.setCurrentIndex(max(0, self.compact_bar.findData(self.bar)))
        for button in self.period_buttons.values():
            button.setVisible(not compact)
        for widget in self.compact_hidden:
            widget.setVisible(not compact)
        self.bottom_button.setVisible(not compact)
        self.sidebar_button.setVisible(not compact)

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
        self.preferences_requested.emit()

    def settings_changed(self, kind, key):
        if kind == "ema":
            self.maybe_warm()
        elif kind == "indicators":
            self.rebuild_indicator_strips()

    def rebuild_indicator_strips(self):
        while self.indicator_layout.count():
            child = self.indicator_layout.takeAt(0).widget()
            if child is not None:
                child.deleteLater()
        self.indicator_strips = []
        for setting in self.indicator_book.indicators:
            if setting["visible"] and setting["kind"] not in ("MA", "EMA", "BOLL", "VWAP", "SUPERTREND"):
                strip = IndicatorStrip(self.canvas, setting["id"], self.indicator_host)
                self.indicator_layout.addWidget(strip)
                self.indicator_strips.append(strip)
        self.indicator_scroll.setVisible(bool(self.indicator_strips))

    def local_indicators_changed(self):
        self.canvas.indicators_override = self.indicator_book.indicators
        self.canvas.calculate_indicators()
        self.canvas._plot_revision += 1
        self.canvas.update()
        self.rebuild_indicator_strips()
        self.context_changed.emit(*self.pair)

    def update_indicator_strips(self):
        for strip in self.indicator_strips:
            strip.update()
        for strip in self.derivative_strips:
            strip.update()

    def toggle_derivatives(self):
        self.derivative_scroll.setVisible(self.derivative_button.isChecked())

    def derivatives_changed(self, instrument):
        if instrument == self.instrument and self.derivative_button.isChecked():
            self.update_indicator_strips()

    def load_older(self):
        if not self.service.closed:
            pair = self.pair
            if pair in self.service.chart_feed.repairs:
                self.service.chart_feed.repair(pair)
                return
            self.service.chart_feed.fetch(*pair, older=True)

    def ensure_view(self):
        if self.isVisible() and self.service.running and not self.service.closed and not self.canvas.follow:
            self.service.chart_feed.ensure_range((self.canvas.instrument, self.canvas.bar), self.canvas.left_time, self.canvas.count)

    def schedule_range(self):
        if self.isVisible():
            self.range_timer.start()

    def retry_history(self):
        self.service.chart_feed.window_attempts.clear()
        self.ensure_view()
        self.load_older()

    def maybe_warm(self):
        if not self.isVisible() or not self.service.running or self.warm_pending:
            return
        pair = self.pair
        rows = self.service.candles.get(pair, [])
        need = max((e["period"]*5 for e in self.service.chart_book.emas if e["visible"]), default=0)
        if rows and len(rows) < need and not self.service.chart_feed.history_state.get(pair):
            self.warm_pending = True
            QTimer.singleShot(350, self.warm_next)

    def warm_next(self):
        self.warm_pending = False
        if self.isVisible() and not self.service.closed:
            pair = self.pair
            need = max((e["period"]*5 for e in self.service.chart_book.emas if e["visible"]), default=0)
            if len(self.service.candles.get(pair, [])) < need:
                self.load_older()

    def set_data(self, rows, title=None):
        self.canvas.set_data(rows)

    def series_changed(self, change):
        if change.pair != self.pair:
            if (change.pair[0] == self.instrument and
                    any(item.get("bar", "chart") == change.pair[1]
                        for item in self.indicator_book.indicators if item.get("visible", True))):
                self.canvas.calculate_indicators()
                self.canvas._plot_revision += 1
                self.canvas.update()
                self.update_indicator_strips()
            return
        previous = self.pending_change
        if previous is not None and previous.pair == change.pair:
            bounds = [t for t in (previous.first, change.first) if t is not None]
            change = replace(change, first=min(bounds) if bounds else None,
                             structural=change.structural or previous.structural)
        self.pending_change = change
        self.queue_refresh("series")

    def queue_refresh(self, kind):
        self.pending_kinds.add(kind)
        if self.isVisible() and not self.refresh_timer.isActive():
            self.refresh_timer.start()

    def refresh(self, kind):
        s = self.service
        if s.closed:
            return
        if kind in ("selection", "environment"):
            if self.symbol_choice.instrument != self.instrument:
                self.symbol_choice.set_instrument(self.instrument)
            self.pending_change = None
            self.canvas.set_context(s.environment, self.instrument, self.bar)
            for bar, btn in self.period_buttons.items():
                btn.setChecked(bar == self.bar)
            self.compact_bar.setCurrentIndex(max(0, self.compact_bar.findData(self.bar)))
            self.tool_buttons["cursor"].setChecked(True)
            self.canvas.tool = "cursor"
        if kind == "candles":
            # 保留直接注入离线数据的兼容入口；正常推送由带版本的通知驱动。
            if self.isVisible():
                series = s.chart_feed.get_series(self.pair)
                if self.canvas._series is not series or self.canvas._series_revision != series.revision:
                    self.queue_refresh("series")
            self.queue_refresh("chart_status")
        elif kind in ("price", "status", "selection", "environment", "account", "orders", "chart_status"):
            self.queue_refresh(kind)

    def flush_refresh(self):
        self.refresh_timer.stop()
        s = self.service
        if s.closed or not self.isVisible():
            return
        kinds, self.pending_kinds = self.pending_kinds, set()
        change, self.pending_change = self.pending_change, None
        all_state = bool(kinds & {"selection", "environment", "show"})
        if all_state or "series" in kinds:
            self.canvas.bind_series(s.chart_feed.get_series(self.pair), None if all_state else change)
            self.update_indicator_strips()
            self.maybe_warm()
            if not self.range_timer.isActive():
                self.range_timer.start()
        repaint = all_state
        if all_state or kinds & {"price", "status"}:
            quote = s.quotes.get(self.instrument, {})
            price = float(quote["price"]) if quote else None
            stale = time.time()-quote.get("time", 0) > 30
            repaint |= (price, stale) != (self.canvas.last_price, self.canvas.price_stale)
            self.canvas.last_price, self.canvas.price_stale = price, stale
        if all_state or kinds & {"account", "orders", "status"}:
            overlays = trade_lines(s) if self.trading else []
            stale = time.time()-s.account.get("time", 0) > 20 or bool(s.account_error)
            repaint |= overlays != self.canvas.overlays or stale != self.canvas.account_stale
            self.canvas.overlays, self.canvas.account_stale = overlays, stale
        status = "OKX · " + s.chart_feed.text(self.pair)
        if self.status.text() != status:
            self.status.setText(status)
            self.status.setToolTip(status+"\n空白拖动平移 · 滚轮缩放 · 拖动右侧价格轴拉伸 · 点击 EMA 图例编辑")
        self.auto_button.setChecked(self.canvas.auto_scale)
        if repaint:
            self.canvas.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.pending_kinds.add("show")
        self.flush_refresh()

    def hideEvent(self, event):
        self.refresh_timer.stop()
        self.range_timer.stop()
        super().hideEvent(event)
