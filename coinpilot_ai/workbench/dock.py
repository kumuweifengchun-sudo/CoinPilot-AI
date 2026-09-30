"""保留 Qt 停靠能力，同时让浮动面板拥有独立的系统窗口。"""
from coinpilot_ai.ui.qt import require
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDockWidget


class IndependentDock(QDockWidget):
    def __init__(self, title, parent):
        super().__init__(title, parent)
        self._configuring_window = False
        self.topLevelChanged.connect(self.sync_window)

    def showEvent(self, a0):
        event = require(a0)
        super().showEvent(event)
        self.sync_window()
        # Qt 可能在 showEvent 返回后的原生显示阶段再次设置 owner。
        QTimer.singleShot(0, self.sync_window)

    def sync_window(self, *_):
        if not self.isFloating() or self._configuring_window:
            return
        self._configuring_window = True
        try:
            # QObject 父子关系继续用于停靠、主题和销毁；系统窗口不再使用 Tool。
            if self.windowType() != Qt.WindowType.Window:
                visible, geometry = self.isVisible(), self.geometry()
                flags = self.windowFlags() & ~Qt.WindowType.WindowType_Mask
                self.setWindowFlags(flags | Qt.WindowType.Window)
                self.setGeometry(geometry)
                if visible:
                    self.show()
            handle = self.windowHandle()
            if handle is None:
                return
            handle.setTransientParent(None)
            if sys.platform == 'win32' and QApplication.platformName() == 'windows':
                import ctypes

                # Qt 的 QWidget 父对象仍会留下原生 owner；只清 transientParent 不够。
                # GWLP_HWNDPARENT 对顶层窗口修改 owner，不改变 Qt 的停靠父对象。
                user32 = ctypes.windll.user32
                set_owner = user32.SetWindowLongPtrW
                set_owner.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
                set_owner.restype = ctypes.c_ssize_t
                set_owner(int(handle.winId()), -8, 0)
        finally:
            self._configuring_window = False
