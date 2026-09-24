import ctypes
from ctypes import wintypes

import pytest
from PyQt6.QtTest import QTest

from coinpilot_ai.config import normalize_hotkey, validate_config
from coinpilot_ai.hotkey import GlobalHotkey


class FakeAPI:
    def __init__(self):
        self.registrations = {}
        self.fail = False

    def RegisterHotKey(self, hwnd, identifier, modifiers, key):
        if self.fail:
            return False
        self.registrations[identifier] = (modifiers, key)
        return True

    def UnregisterHotKey(self, hwnd, identifier):
        self.registrations.pop(identifier, None)
        return True


@pytest.mark.parametrize("value", ["", "Z", "Shift+Z", "Alt+", "Alt+Alt+Z", "Alt+F25", "Alt+Z, Alt+X"])
def test_invalid_hotkeys(value):
    with pytest.raises(ValueError):
        normalize_hotkey(value)


def test_legacy_hotkey_default_and_normalization():
    assert validate_config({})[0]["hide_hotkey"] == "Alt+Z"
    assert normalize_hotkey(" shift + ctrl + h ") == "Ctrl+Shift+H"
    assert validate_config({"hide_hotkey": "bad"})[0]["hide_hotkey"] == "Alt+Z"


def test_registration_conflict_and_save_failure_keep_old_hotkey(app):
    api = FakeAPI()
    hotkey = GlobalHotkey(lambda: None, api=api)
    try:
        hotkey.change("Alt+Z")
        original = api.registrations.copy()
        api.fail = True
        with pytest.raises(ValueError, match="占用"):
            hotkey.change("Ctrl+H")
        assert api.registrations == original
        api.fail = False
        def fail_save():
            raise PermissionError("保存失败")
        with pytest.raises(PermissionError):
            hotkey.change("Ctrl+H", fail_save)
        assert api.registrations == original
        assert hotkey.shortcut == "Alt+Z"
        hotkey.change("Ctrl+Shift+H")
        assert len(api.registrations) == 1
        assert api.registrations[hotkey.active_id] == (0x4000 | 2 | 4, ord("H"))
    finally:
        hotkey.close()
    assert not api.registrations


def test_native_message_dispatch_and_closed_callbacks(app):
    results = []
    hotkey = GlobalHotkey(lambda: results.append(True), api=FakeAPI())
    hotkey.change("Alt+Z")
    message = wintypes.MSG()
    message.message, message.wParam = 0x0312, hotkey.active_id
    assert hotkey.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(message)) == (True, 0)
    QTest.qWait(10)
    assert results == [True]
    hotkey.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(message))
    hotkey.close()
    QTest.qWait(10)
    assert results == [True]
