"""桌面悬浮窗、轮播与用户交互。"""

from PyQt6.QtCore import QEvent, QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QActionGroup, QColor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from coinpilot_ai.market.client import MarketClient
from coinpilot_ai.core.config import SOURCE_LABELS
from coinpilot_ai.market.providers import SOURCE_NAMES
from .settings import SettingsDialog
from coinpilot_ai.ui.icons import icon
from coinpilot_ai.ui.theme import activate_theme, color, events, menu_style
from coinpilot_ai.desktop.visuals import Quote, draw_ticker, ticker_size
from coinpilot_ai.ui.typography import font, render_hints


class CoinPilotWidget(QWidget):
    workbench_requested = pyqtSignal()
    configuration_changed = pyqtSignal(object)
    update_requested = pyqtSignal()
    visibility_changed = pyqtSignal(bool)

    def __init__(self, config, store, client=None, start_requests=True, startup=None):
        super().__init__()
        self.config = config.copy()
        self.store = store
        self.startup = startup
        self.client = client if client is not None else MarketClient(parent=self)
        self.quotes = {}
        self.current_index = 0
        self.previous_index = 0
        self.progress = 1.0
        self._drag_pos = None
        self._press_pos = None
        self._pointer_pos = None
        self._click_allowed = False
        self._press_window_pos = None
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.setInterval(350)
        self.hold_timer.timeout.connect(self._begin_drag)
        self._closed = False
        self.unread_events = 0
        self._minimum_content_width = 0
        self.settings_dialog = None
        self.settings_router = None
        self.hotkey = None
        self._settings_hidden = False
        self.setWindowTitle("CoinPilot AI · 币航")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(300)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(self._animate)
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_prices)
        self.cycle_timer = QTimer(self)
        self.cycle_timer.timeout.connect(self.start_slide)
        self.client.price_ready.connect(self._price_ready)
        self.client.icon_ready.connect(self._icon_ready)
        events.changed.connect(self.apply_theme)
        self.apply_settings(config, request=start_requests)
        self.move(config["pos_x"], config["pos_y"])
        self.ensure_visible()
        app = QApplication.instance()
        app.aboutToQuit.connect(self.shutdown)
        app.screenRemoved.connect(lambda _: self.ensure_visible())
        for screen in app.screens():
            screen.availableGeometryChanged.connect(lambda _: self.ensure_visible())
        app.screenAdded.connect(lambda s: s.availableGeometryChanged.connect(lambda _: self.ensure_visible()))

    @property
    def symbols(self):
        return [self.config[f"symbol{i}"] for i in range(1, 4)]

    @property
    def decimals(self):
        return [self.config[f"decimals{i}"] for i in range(1, 4)]

    def apply_settings(self, config, request=True):
        activate_theme(config["ui_theme"])
        self.client.set_proxy(config)
        self.client.cancel("price")
        self.client.set_source(config.get("price_source", "auto"))
        self.animation.stop()
        self.current_index = self.previous_index = 0
        self.progress = 1.0
        self.config = config.copy()
        self.quotes = {symbol: self.quotes.get(symbol, Quote(symbol)) for symbol in self.symbols}
        self._minimum_content_width = 0
        self._resize_to_content()
        self.update_timer.start(self.config["update_interval"] * 1000)
        self.cycle_timer.stop()
        if self.config["cycle_enabled"]:
            self.cycle_timer.start(self.config["cycle_interval"] * 1000)
        if request:
            self.reload_icons()
            self.update_prices()
        self.update()
        self.configuration_changed.emit(self.config.copy())

    def apply_theme(self, _theme_id):
        self.update()

    def update_prices(self):
        if not self._closed:
            self.client.refresh_prices(self.symbols)

    def save_configuration(self, candidate, startup_enabled=None):
        def save():
            if startup_enabled is not None and self.startup is not None:
                self.startup.save_with(startup_enabled, lambda: self.store.save(candidate))
            else:
                self.store.save(candidate)

        if self.hotkey is not None:
            self.hotkey.change(candidate["hide_hotkey"], save)
        else:
            save()

    def toggle_visibility(self):
        if self._closed:
            return
        if self.isVisible():
            self._settings_hidden = self.settings_dialog is not None and self.settings_dialog.isVisible()
            if self._settings_hidden:
                self.settings_dialog.hide()
            self.hide()
        else:
            self.show()
            self.ensure_visible()
            if self._settings_hidden and self.settings_dialog is not None:
                self.settings_dialog.show()
                self.settings_dialog.raise_()
            self._settings_hidden = False

    def reload_icons(self, clear=False):
        if clear:
            for quote in self.quotes.values():
                quote.icon = None
            self.update()
        self.client.reload_icons(self.symbols, clear=clear)

    def _price_ready(self, symbol, price, error, source=""):
        if symbol in self.quotes and not self._closed:
            self.quotes[symbol].accept(price, error, source)
            self._resize_to_content()
            self.update()

    def _icon_ready(self, symbol, image):
        if symbol in self.quotes and not self._closed:
            self.quotes[symbol].icon = QPixmap.fromImage(image)
            self.update()

    def _resize_to_content(self):
        width, height = ticker_size([self.quotes[s] for s in self.symbols], self.decimals,
                                    self.config["text_size"], mini=self.config.get("mini_mode", True), device=self)
        self._minimum_content_width = max(width, self._minimum_content_width)
        self.logical_width = self._minimum_content_width
        self.logical_height = height
        area = self.screen().availableGeometry()
        scale = min(1, max(1, area.width() - 16) / self.logical_width)
        self.resize(round(self.logical_width * scale), round(height * scale))
        self.ensure_visible()
        self._update_tooltip()

    def _update_tooltip(self):
        quote = self.quotes[self.symbols[self.current_index]]
        shortcut = self.config.get("hide_hotkey", "Alt+Z")
        origin = SOURCE_NAMES.get(quote.source, "暂无")
        mode = SOURCE_LABELS[self.config.get("price_source", "auto")]
        details = f"\n失败原因：{quote.error}" if quote.error else ""
        self.setToolTip(f"{quote.symbol}\n{quote.status_text()}\n报价来源：{origin} · 当前模式：{mode}"
                       f"{details}\n单击刷新 · 双击打开交易台\n长按拖动 · 右键打开设置\n{shortcut} 隐藏／显示"
                       + (f"\n工作台有 {self.unread_events} 条新提醒" if self.unread_events else ""))

    def set_unread_events(self, count):
        self.unread_events = max(0, int(count))
        self._update_tooltip()
        self.update()

    def set_price_source(self, source):
        candidate = {**self.config, "price_source": source}
        try:
            self.store.save(candidate)
        except (OSError, ValueError):
            self.setToolTip("行情数据源保存失败，请在设置中重试。")
            return
        self.apply_settings(candidate)

    def set_mini_mode(self, enabled):
        candidate = {**self.config, "mini_mode": enabled}
        try:
            self.store.save(candidate)
        except (OSError, ValueError):
            self.setToolTip("显示模式保存失败，请在设置中重试。")
            return
        self.config = candidate
        self.animation.stop()
        self.progress = 1.0
        self._minimum_content_width = 0
        self._resize_to_content()
        self.update()

    def ensure_visible(self):
        screens = QApplication.screens()
        if not screens:
            return
        target = next((s for s in screens if s.availableGeometry().contains(self.frameGeometry().center())), None)
        if target is None:
            target = QApplication.primaryScreen()
        area = target.availableGeometry()
        x = min(max(self.x(), area.left()), max(area.left(), area.right() - self.width() + 1))
        y = min(max(self.y(), area.top()), max(area.top(), area.bottom() - self.height() + 1))
        self.move(x, y)

    def start_slide(self):
        if not self.config["cycle_enabled"] or self.animation.state() == QVariantAnimation.State.Running:
            return
        self.previous_index = self.current_index
        self.current_index = (self.current_index + 1) % len(self.symbols)
        self._update_tooltip()
        self.progress = 0.0
        self.animation.start()

    def _animate(self, value):
        self.progress = value
        self.update()

    def event(self, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.visibility_changed.emit(event.type() == QEvent.Type.Show)
        if event.type() in (QEvent.Type.Hide, QEvent.Type.UngrabMouse) and hasattr(self, "hold_timer"):
            self._cancel_pointer()
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and getattr(self, "quotes", None):
            QTimer.singleShot(0, self._refresh_screen_metrics)
        return result

    def _refresh_screen_metrics(self):
        if not self._closed:
            self._resize_to_content()
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        render_hints(painter)
        painter.scale(self.width() / self.logical_width, self.height() / self.logical_height)
        rect = QRectF(0, 0, self.logical_width, self.logical_height)
        clip = QPainterPath()
        radius = 7 if self.config.get("mini_mode", True) else 16
        clip.addRoundedRect(rect, radius, radius)
        painter.setClipPath(clip)
        entries = [(self.current_index, (1 - self.progress) * self.logical_width)]
        if self.progress < 1:
            entries.insert(0, (self.previous_index, -self.progress * self.logical_width))
        for index, offset in entries:
            painter.save()
            painter.translate(offset, 0)
            draw_ticker(painter, rect, self.quotes[self.symbols[index]], self.decimals[index],
                        self.config["text_size"], self.config["bg_opacity"], mini=self.config.get("mini_mode", True))
            painter.restore()
        if self.unread_events:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color("warning")))
            painter.drawEllipse(QRectF(self.logical_width - 8, 3, 4, 4))
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._pointer_pos = self._press_pos
            self._press_window_pos = self.pos()
            self._click_allowed = True
            self.hold_timer.start()

    def _begin_drag(self):
        if self._press_pos is not None and not self._closed:
            self._click_allowed = False
            self._drag_pos = self._pointer_pos - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._closed:
            # 双击的第二次按下不再进入拖动，也不在随后松开时再次刷新。
            self._cancel_pointer()
            event.accept()
            self.workbench_requested.emit()
        else:
            super().mouseDoubleClickEvent(event)

    def _cancel_pointer(self):
        self.hold_timer.stop()
        self._drag_pos = self._press_pos = self._pointer_pos = None
        self._press_window_pos = None
        self._click_allowed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._press_pos is not None:
            self._pointer_pos = event.globalPosition().toPoint()
            if (self._pointer_pos - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
                self._click_allowed = False
            if self._drag_pos is not None:
                self.move(self._pointer_pos - self._drag_pos)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        clicked = (self._click_allowed and self.rect().contains(event.position().toPoint())
                   and (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
                   < QApplication.startDragDistance())
        moved = self._drag_pos is not None and self.pos() != self._press_window_pos
        self._cancel_pointer()
        if clicked:
            self.update_prices()
        if not moved:
            return
        self.ensure_visible()
        candidate = {**self.config, "pos_x": self.x(), "pos_y": self.y()}
        try:
            self.store.save(candidate)
            self.config = candidate
        except (OSError, ValueError):
            self.setToolTip("窗口位置保存失败，请检查配置文件权限。")

    def _create_context_menu(self):
        menu = QMenu(self)
        menu.setFont(font(11))
        menu.setStyleSheet(menu_style() + "QMenu {font-size:11px;} QMenu::item {padding:6px 24px 6px 10px;}")
        if self.settings_router is None:  # 工作台不可用时保留独立窗口的恢复入口。
            mini = menu.addAction("迷你模式")
            mini.setIcon(icon("mini"))
            mini.setCheckable(True)
            mini.setChecked(self.config.get("mini_mode", True))
            mini.triggered.connect(self.set_mini_mode)
            source_menu = menu.addMenu("行情数据源")
            source_menu.setIcon(icon("activity"))
            source_menu.setFont(font(11))
            source_group = QActionGroup(source_menu)
            source_group.setExclusive(True)
            for source, label in SOURCE_LABELS.items():
                source_action = source_menu.addAction(label)
                source_action.setCheckable(True)
                source_action.setChecked(source == self.config.get("price_source", "auto"))
                source_group.addAction(source_action)
                source_action.triggered.connect(lambda checked, value=source: self.set_price_source(value))
        settings = menu.addAction("设置")
        settings.setIcon(icon("settings"))
        settings.triggered.connect(self.open_settings)
        workbench = menu.addAction("交易台" + (f"（{self.unread_events} 条新提醒）" if self.unread_events else ""))
        workbench.setIcon(icon("workbench"))
        workbench.triggered.connect(self.workbench_requested.emit)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.setIcon(icon("power"))
        quit_action.triggered.connect(QApplication.instance().quit)
        return menu

    def contextMenuEvent(self, event):
        self._cancel_pointer()
        menu = self._create_context_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()

    def open_settings(self):
        if self.settings_router is not None:
            self.settings_router()
            return
        if self.settings_dialog is not None:
            self.settings_dialog.showNormal()
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.finished.connect(self._settings_finished)
        self.settings_dialog.show()

    def _settings_finished(self, result):
        if self.settings_dialog is not None:
            self.settings_dialog.deleteLater()
        self.settings_dialog = None
        self._settings_hidden = False

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_pointer()
        self.update_timer.stop()
        self.cycle_timer.stop()
        self.animation.stop()
        if self.hotkey is not None:
            self.hotkey.close()
        self.client.close()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
