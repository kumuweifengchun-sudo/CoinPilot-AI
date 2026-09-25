"""工作台标题栏：页面导航、窗口操作及 Windows 原生拖动／缩放。"""
import sys

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QDockWidget, QHBoxLayout,
                            QLabel, QMenu, QToolButton, QWidget)

from coinpilot_ai.ui.icons import application_icon, icon
from coinpilot_ai.ui.theme import color


class WorkbenchTitleBar(QWidget):
    HEIGHT = 40

    def __init__(self, owner, pages):
        super().__init__(owner)
        self.owner, self.pages = owner, pages
        self._native_handle = None
        self.setObjectName('workbenchTitleBar')
        self.setFixedHeight(self.HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 6, 0)
        row.setSpacing(2)
        self.logo = QLabel()
        self.logo.setFixedSize(24, 24)
        self.logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.logo)
        row.addSpacing(8)
        self.view_button = QToolButton()
        self.view_button.setText('视图')
        self.view_button.setAccessibleName('视图')
        self.view_menu = QMenu(self)
        self.view_button.setMenu(self.view_menu)
        self.view_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.group = QButtonGroup(self)
        self.navigation = []
        for index, text in enumerate(('工作台', '复盘', '设置')):
            button = QToolButton()
            button.setText(text)
            button.setAccessibleName(text)
            button.setCheckable(True)
            self.group.addButton(button, index)
            row.addWidget(button)
            self.navigation.append(button)
            if index == 1:
                row.addWidget(self.view_button)
        self.group.idClicked.connect(pages.setCurrentIndex)
        pages.currentChanged.connect(self.sync_page)
        self.sync_page(pages.currentIndex())
        row.addStretch()
        self.caption = QLabel(owner.windowTitle())
        self.caption.setObjectName('windowCaption')
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.caption)
        owner.windowTitleChanged.connect(self.caption.setText)
        row.addStretch()
        self.minimize_button = self.window_button('最小化', owner.showMinimized)
        self.maximize_button = self.window_button('最大化', self.toggle_maximized)
        self.close_button = self.window_button('关闭（后台继续运行）', owner.close)
        self.close_button.setObjectName('windowClose')
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            row.addWidget(button)
        owner.installEventFilter(self)
        self.apply_theme()

    def bind_workspace(self, workspace):
        self.view_menu.addActions(list(workspace.actions.values()))
        self.view_menu.addSeparator()
        self.view_menu.addAction('工作区设置…', lambda: self.owner.open_settings('工作区布局'))

    def window_button(self, text, callback):
        button = QToolButton()
        button.setFixedSize(42, self.HEIGHT - 8)
        button.setProperty('windowControl', True)
        button.setIconSize(QSize(18, 18))
        button.setAccessibleName(text)
        button.setToolTip(text)
        button.clicked.connect(callback)
        return button

    def sync_page(self, index):
        if 0 <= index < len(self.navigation):
            self.navigation[index].setChecked(True)

    def sync_controls(self):
        maximized = self.owner.isMaximized()
        self.minimize_button.setIcon(icon('window-minimize'))
        self.maximize_button.setIcon(icon('window-restore' if maximized else 'window-maximize'))
        self.maximize_button.setToolTip('还原' if maximized else '最大化')
        self.maximize_button.setAccessibleName(self.maximize_button.toolTip())
        self.close_button.setIcon(icon('window-close'))

    def apply_theme(self):
        self.setStyleSheet(
            f'QWidget#workbenchTitleBar {{background:{color("surface")};}}'
            'QToolButton {border:0; border-radius:4px; padding:6px 12px; background:transparent;}'
            'QToolButton[windowControl="true"] {padding:0;}'
            f'QToolButton:hover {{background:{color("surface_hover")};}}'
            f'QToolButton:pressed {{background:{color("surface_selected")};}}'
            f'QToolButton:checked {{background:{color("surface_selected")};color:{color("text")};}}'
            'QToolButton::menu-indicator {width:0;}'
            f'QLabel#windowCaption {{color:{color("text_muted")};}}'
            'QToolButton#windowClose:hover {background:#C42B3B;}'
            'QToolButton#windowClose:pressed {background:#A92331;}')
        self.logo.setPixmap(application_icon().pixmap(QSize(24, 24), self.devicePixelRatioF()))
        self.sync_controls()

    def toggle_maximized(self):
        if self.owner.isMaximized():
            self.owner.showNormal()
        else:
            self.owner.showMaximized()

    def is_drag_region(self, point):
        if not self.rect().contains(point):
            return False
        child = self.childAt(point)
        while child is not None and child is not self:
            if isinstance(child, QAbstractButton):
                return False
            child = child.parentWidget()
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drag_region(event.position().toPoint()):
            if self.owner.windowHandle():
                self.owner.windowHandle().startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drag_region(event.position().toPoint()):
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.owner:
            if event.type() == QEvent.Type.WindowStateChange:
                self.sync_controls()
            elif event.type() == QEvent.Type.DevicePixelRatioChange:
                self.apply_theme()
        return super().eventFilter(watched, event)

    def setup_native_frame(self):
        if sys.platform != 'win32' or QApplication.platformName() != 'windows':
            return
        import ctypes
        handle = int(self.owner.winId())
        if self._native_handle == handle:
            return
        self._native_handle = handle
        user32 = ctypes.windll.user32
        hwnd = ctypes.c_void_p(handle)
        # 保留系统调整大小、任务栏操作及边缘停靠能力；客户区绘制自己的标题栏。
        style = user32.GetWindowLongW(hwnd, -16)
        user32.SetWindowLongW(hwnd, -16, style | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)

    def native_event(self, message):
        if sys.platform != 'win32' or QApplication.platformName() != 'windows':
            return None
        import ctypes
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        user32 = ctypes.windll.user32
        hwnd = ctypes.c_void_p(msg.hWnd)
        if msg.message == 0x0083 and msg.wParam:  # WM_NCCALCSIZE
            if user32.IsZoomed(hwnd):
                class MonitorInfo(ctypes.Structure):
                    _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                                ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
                user32.MonitorFromWindow.restype = wintypes.HANDLE
                monitor = user32.MonitorFromWindow(hwnd, 2)
                info = MonitorInfo()
                info.size = ctypes.sizeof(info)
                if user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
                    rect = wintypes.RECT.from_address(msg.lParam)
                    rect.left, rect.top, rect.right, rect.bottom = (
                        info.work.left, info.work.top, info.work.right, info.work.bottom)
            return True, 0
        if msg.message == 0x0084:  # WM_NCHITTEST，坐标为屏幕物理像素（含负坐标）。
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            x = ctypes.c_short(msg.lParam & 0xffff).value - rect.left
            y = ctypes.c_short((msg.lParam >> 16) & 0xffff).value - rect.top
            dpr = self.owner.devicePixelRatioF()
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if not self.owner.isMaximized() and not self.owner.isFullScreen():
                border = round(6 * dpr)
                left, right = x < border, x >= width - border
                top, bottom = y < border, y >= height - border
                edge = (13 if left else 14 if right else 12) if top else (
                    (16 if left else 17 if right else 15) if bottom else (10 if left else 11 if right else 0))
                if edge:
                    return True, edge
            point = self.mapFrom(self.owner, QPoint(round(x / dpr), round(y / dpr)))
            return True, 2 if self.is_drag_region(point) else 1  # HTCAPTION / HTCLIENT
        return None


class DockTitleBar(QWidget):
    """固定逻辑尺寸的面板操作区；空白处事件交回 Qt 处理拖动和双击。"""

    def __init__(self, dock):
        super().__init__(dock)
        self.setObjectName('dockTitleBar')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(28)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 4, 2)
        row.setSpacing(2)
        label = QLabel(dock.windowTitle())
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        dock.windowTitleChanged.connect(label.setText)
        row.addWidget(label, 1)
        self.float_button = QToolButton(self)
        self.close_button = QToolButton(self)
        for button, name in ((self.float_button, 'window-restore'), (self.close_button, 'window-close')):
            button.setFixedSize(24, 24)
            button.setIconSize(QSize(16, 16))
            button.setIcon(icon(name))
            row.addWidget(button)
        self.close_button.setToolTip('关闭面板')
        self.close_button.setAccessibleName('关闭面板')
        self.close_button.clicked.connect(dock.close)
        self.float_button.clicked.connect(lambda: dock.setFloating(not dock.isFloating()))
        dock.topLevelChanged.connect(self.sync_controls)
        dock.featuresChanged.connect(self.sync_controls)
        self.sync_controls()

    def sync_controls(self, *_):
        dock = self.parentWidget()
        features = dock.features()
        self.close_button.setVisible(bool(features & QDockWidget.DockWidgetFeature.DockWidgetClosable))
        self.float_button.setVisible(bool(features & QDockWidget.DockWidgetFeature.DockWidgetFloatable))
        text = '停靠面板' if dock.isFloating() else '浮动面板'
        self.float_button.setToolTip(text)
        self.float_button.setAccessibleName(text)
