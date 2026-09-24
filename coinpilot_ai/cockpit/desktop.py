"""应用级生命周期与系统托盘，迷你窗口继续作为默认入口。"""
import sqlite3

from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget

from ..icons import icon
from ..theme import color, events, menu_style, style_sheet
from .service import CockpitService
from .workbench import Workbench


class NotificationToast(QWidget):
    """无系统提示音、不抢焦点的单个通知卡片；新通知替换当前卡片。"""
    def __init__(self, open_workbench):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(360)
        self.apply_theme()
        events.changed.connect(self.apply_theme)
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        view, dismiss = QPushButton("查看事件"), QPushButton("关闭")
        view.clicked.connect(lambda: (self.hide(), open_workbench()))
        dismiss.clicked.connect(self.hide)
        row.addWidget(view)
        row.addWidget(dismiss)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.important = False
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def display(self, title, detail, screen, important=False):
        self.important = important
        self.title.setText(title)
        self._style_title()
        self.detail.setText(detail)
        self.adjustSize()
        area = screen.availableGeometry()
        self.move(area.right()-self.width()-16, area.bottom()-self.height()-16)
        self.show()
        self.timer.start(10000 if important else 7000)

    def apply_theme(self, _theme_id=None):
        self.setStyleSheet(style_sheet() + f"NotificationToast {{background:{color('surface_raised')};"
                           f"border:1px solid {color('border_strong')};border-radius:9px;}}")
        if hasattr(self, "title"):
            self._style_title()

    def _style_title(self):
        self.title.setStyleSheet("font-weight:bold;color:" + color("warning" if self.important else "text") + ";")


class DesktopController(QObject):
    def __init__(self, app, widget, config_path, cache_dir):
        super().__init__(app)
        self.app, self.widget = app, widget
        self._closing = False
        self.window = None
        self.warning = ""
        self.service = None
        self.toast = NotificationToast(self.open_workbench)
        self.tray = QSystemTrayIcon(app)
        self.menu = QMenu()
        self.menu.setStyleSheet(menu_style())
        events.changed.connect(self.refresh_theme)
        self.status_action = self.menu.addAction("CoinPilot AI · 后台运行中")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.workbench_action = self.menu.addAction("打开交易工作台", self.open_workbench)
        self.workbench_action.setIcon(icon("workbench"))
        self.menu.setDefaultAction(self.workbench_action)
        self.visibility_action = self.menu.addAction("显示迷你窗口", widget.toggle_visibility)
        self.settings_action = self.menu.addAction("迷你窗口设置", widget.open_settings)
        self.settings_action.setIcon(icon("settings"))
        self.pause_action = self.menu.addAction("暂停弹出通知")
        self.pause_action.setIcon(icon("bell-off"))
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.pause_notifications)
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction("退出程序", app.quit)
        self.quit_action.setIcon(icon("power"))
        self.tray.setContextMenu(self.menu)
        self.update_tray()
        self.tray.activated.connect(self.activated)
        self.tray.messageClicked.connect(self.open_workbench)
        self.tray.show()
        widget.workbench_requested.connect(self.open_workbench)
        widget.configuration_changed.connect(self.configuration_changed)
        widget.visibility_changed.connect(self.update_tray)
        self.menu.aboutToShow.connect(self.update_tray)
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.show_mini)
        try:
            self.service = CockpitService(widget.config, config_path.with_suffix(".workbench.sqlite3"), cache_dir, parent=app)
            self.service.event_created.connect(self.notify)
            self.service.updated.connect(self.updated)
            self.updated("settings")
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.warning = "工作台启动失败，迷你窗口与托盘仍可使用：" + str(exc)
            self.workbench_action.setEnabled(False)
            self.pause_action.setEnabled(False)
        self.update_tray()
        app.setQuitOnLastWindowClosed(False)
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.close)

    def eventFilter(self, watched, event):
        if watched is self.app and event.type() == QEvent.Type.Quit and self.window is not None:
            self.window.exiting = True
        return False

    def open_workbench(self):
        if self.service is None:
            self.show_mini()
            return
        if self.window is None:
            self.window = Workbench(self.service, self.widget.open_settings)
            self.window.events_seen.connect(self.clear_badge)
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.clear_badge()

    def activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.click_timer.stop()
            self.open_workbench()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.click_timer.start(QApplication.doubleClickInterval())

    def show_mini(self):
        self.widget.show()
        self.widget.ensure_visible()
        self.widget.raise_()

    def configuration_changed(self, config):
        if self.service is not None:
            self.service.set_proxy(config)
        self.update_tray()

    def update_tray(self, *_):
        if self._closing:
            return
        paused = self.service is not None and self.service.settings.get("notifications_paused", False)
        count = self.widget.unread_events
        status = "通知已暂停" if paused else "后台运行中"
        if self.service is None:
            status = "迷你行情运行中 · 工作台不可用"
        self.status_action.setText("CoinPilot AI · " + status)
        self.tray.setToolTip("CoinPilot AI · " + status + (f"\n{count} 条未读提醒" if count else "") + "\n单击显示迷你窗口 · 双击打开工作台")
        self.visibility_action.setText("隐藏迷你窗口" if self.widget.isVisible() else "显示迷你窗口")
        self.visibility_action.setIcon(icon("eye-off" if self.widget.isVisible() else "eye"))
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("surface_raised")))
        painter.drawRoundedRect(3, 3, 58, 58, 14, 14)
        icon("brand", color("text")).paint(painter, 12, 12, 40, 40)
        if count or paused:
            painter.setBrush(QColor(color("warning" if paused else "positive")))
            painter.setPen(QColor(color("surface_raised")))
            painter.drawEllipse(45, 3, 16, 16)
        painter.end()
        self.tray.setIcon(QIcon(pixmap))

    def refresh_theme(self, _theme_id=None):
        self.menu.setStyleSheet(menu_style())
        self.update_tray()

    def pause_notifications(self, paused):
        if self.service is None:
            return
        self.service.settings["notifications_paused"] = paused
        self.service.save_settings()
        self.update_tray()

    def updated(self, kind):
        if kind == "settings":
            self.pause_action.blockSignals(True)
            self.pause_action.setChecked(self.service.settings.get("notifications_paused", False))
            self.pause_action.blockSignals(False)
            if self.service.settings.get("notifications_paused"):
                self.toast.hide()
            self.update_tray()

    def clear_badge(self):
        self.widget.set_unread_events(0)
        self.update_tray()

    def notify(self, key, event):
        self.widget.set_unread_events(self.widget.unread_events + 1)
        self.update_tray()
        if self.service.settings.get("notifications_paused"):
            return
        priority = event.get("priority") in ("position", "order")
        title = ("持仓／订单 · " if priority else "行情 · ") + event["name"]
        self.toast.display(title, event.get("instrument", "") + "\n打开工作台查看触发依据与分析", self.widget.screen(), priority)
        if priority and self.service.settings.get("sound"):
            QApplication.beep()

    def close(self):
        if self._closing:
            return
        self._closing = True
        self.click_timer.stop()
        self.tray.hide()
        self.tray.setContextMenu(None)
        if self.window is not None:
            self.window.exiting = True
            self.window.close()
            self.window.deleteLater()
            self.window = None
        self.toast.close()
        self.toast.deleteLater()
        self.menu.close()
        self.menu.deleteLater()
        self.tray.deleteLater()
        if self.service is not None:
            self.service.close()
            self.service.deleteLater()
