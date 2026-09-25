"""Windows 全局快捷键；切换失败时保留原注册，退出时释放。"""

import ctypes
from ctypes import wintypes
import sys

from PyQt6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QTimer

from coinpilot_ai.core.config import normalize_hotkey


class GlobalHotkey(QAbstractNativeEventFilter):
    WM_HOTKEY = 0x0312
    MOD_NOREPEAT = 0x4000

    def __init__(self, callback, api=None):
        super().__init__()
        if api is None:
            if sys.platform != "win32":
                raise ValueError("全局快捷键目前仅支持 Windows。")
            api = ctypes.WinDLL("user32", use_last_error=True)
            api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            api.RegisterHotKey.restype = wintypes.BOOL
            api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            api.UnregisterHotKey.restype = wintypes.BOOL
        self.api = api
        self.callback = callback
        self.shortcut = None
        self.active_id = None
        self.closed = False
        QCoreApplication.instance().installNativeEventFilter(self)

    def change(self, shortcut, persist=lambda: None):
        shortcut = normalize_hotkey(shortcut)
        if shortcut == self.shortcut:
            persist()
            return
        parts = shortcut.split("+")
        modifiers = self.MOD_NOREPEAT
        for modifier in parts[:-1]:
            modifiers |= {"Alt": 1, "Ctrl": 2, "Shift": 4, "Win": 8}[modifier]
        key = parts[-1]
        virtual_key = 0x70 + int(key[1:]) - 1 if key.startswith("F") and len(key) > 1 else ord(key)
        # 先注册新组合，成功保存后才释放旧组合，避免冲突时失去恢复入口。
        new_id = 0x4357 if self.active_id != 0x4357 else 0x4358
        if not self.api.RegisterHotKey(None, new_id, modifiers, virtual_key):
            raise ValueError(f"快捷键 {shortcut} 无法注册，可能已被其他程序占用，请换一个组合。")
        try:
            persist()
        except Exception:
            self.api.UnregisterHotKey(None, new_id)
            raise
        previous_id = self.active_id
        self.active_id, self.shortcut = new_id, shortcut
        if previous_id is not None:
            self.api.UnregisterHotKey(None, previous_id)

    def nativeEventFilter(self, event_type, message):
        if not self.closed and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_HOTKEY and msg.wParam == self.active_id:
                current_id = self.active_id
                QTimer.singleShot(0, lambda: self.callback()
                                 if not self.closed and self.active_id == current_id else None)
                return True, 0
        return False, 0

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.active_id is not None:
            self.api.UnregisterHotKey(None, self.active_id)
            self.active_id = None
        QCoreApplication.instance().removeNativeEventFilter(self)
