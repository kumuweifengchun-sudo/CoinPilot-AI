from decimal import Decimal

import pytest
from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QFontInfo, QImage, QMouseEvent, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from coinpilot_ai.config import DEFAULT_CONFIG, SettingsStore
from coinpilot_ai.settings import SettingsDialog
from coinpilot_ai.visuals import FONT_FAMILY, Quote, draw_ticker, font, ticker_size
from coinpilot_ai.widget import CoinPilotWidget


class StubClient(QObject):
    price_ready = pyqtSignal(str, object, str, str)
    icon_ready = pyqtSignal(str, QImage)
    icons_finished = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.closed = False
        self.cancelled = []
        self.source = "auto"

    def set_source(self, source):
        self.source = source

    def set_proxy(self, config):
        self.proxy_config = {key: value for key, value in config.items() if key.startswith("proxy_")}

    def cancel(self, kind):
        self.cancelled.append(kind)

    def refresh_prices(self, symbols):
        pass

    def reload_icons(self, symbols, clear=False):
        self.icons_finished.emit(0, len(set(symbols)))

    def close(self):
        self.closed = True


def test_bundled_inter_font_is_used(app):
    assert app.inter_font_loaded
    assert QFontInfo(app.font()).family() == FONT_FAMILY
    assert QFontInfo(font(12, True, latin=True)).family() == FONT_FAMILY


@pytest.fixture
def widget(app, tmp_path):
    instance = CoinPilotWidget(DEFAULT_CONFIG, SettingsStore(tmp_path / "settings.json"), StubClient())
    yield instance
    instance.close()
    instance.deleteLater()
    app.processEvents()


def test_preview_draft_cancel_and_normalization(widget, app):
    dialog = SettingsDialog(widget)
    dialog.show()
    dialog.symbol_edits[0].setText(" hypeusdt ")
    dialog.mini_check.setChecked(False)
    dialog.number_inputs["text_size"].setValue(36)
    dialog.number_inputs["bg_opacity"].setValue(50)
    assert dialog.preview.config["symbol1"] == "HYPEUSDT"
    assert dialog.preview.config["text_size"] == 36
    assert dialog.preview.config["bg_opacity"] == 0.5
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert widget.config == DEFAULT_CONFIG
    assert not widget.store.path.exists()
    dialog.deleteLater()


def test_save_and_numeric_input_limits(widget):
    dialog = SettingsDialog(widget)
    assert {key: (slider.minimum(), slider.maximum()) for key, slider in dialog.number_inputs.items()} == {
        "text_size": (8, 64), "bg_opacity": (0, 100), "update_interval": (5, 300), "cycle_interval": (3, 60)}
    assert all(number.suffix() for number in dialog.number_inputs.values())
    dialog.symbol_edits[0].setText(" hypeusdt ")
    dialog.show()
    refresh_input = dialog.number_inputs["update_interval"]
    refresh_input.setFocus()
    refresh_input.selectAll()
    QTest.keyClicks(refresh_input, "25")
    dialog.cycle_check.setChecked(False)
    assert not dialog.number_inputs["cycle_interval"].isEnabled()
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert widget.config["symbol1"] == "HYPEUSDT"
    assert widget.store.load() == widget.config
    assert widget.update_timer.interval() == 25000
    assert not widget.cycle_timer.isActive()
    dialog.deleteLater()


def test_invalid_input_and_save_failure_keep_dialog_open(widget, monkeypatch):
    dialog = SettingsDialog(widget)
    dialog.show()
    dialog.symbol_edits[0].setText("")
    dialog._save()
    assert "第 1 个交易对" in dialog.feedback.text()
    assert dialog.isVisible()
    dialog.symbol_edits[0].setText("BTCUSDT")
    def fail(_):
        raise PermissionError("测试")
    monkeypatch.setattr(widget.store, "save", fail)
    dialog.number_inputs["text_size"].setValue(42)
    dialog._save()
    assert "保存失败" in dialog.feedback.text()
    assert dialog.isVisible()
    assert widget.config["text_size"] == 24
    dialog.close()
    dialog.deleteLater()


def test_failed_price_retains_last_success_and_recovers(widget):
    quote = widget.quotes["BTCUSDT"]
    assert quote.price_text(2) == "加载中"
    widget._price_ready("BTCUSDT", None, "网络请求失败")
    assert quote.price_text(2) == "暂无数据"
    widget._price_ready("BTCUSDT", Decimal("123.45"), "")
    widget._price_ready("BTCUSDT", None, "网络请求失败")
    assert quote.price == Decimal("123.45")
    assert "保留上次价格" in quote.status_text()
    widget._price_ready("BTCUSDT", Decimal("125.45"), "")
    assert quote.error == ""
    assert "更新于" in quote.status_text()


def test_apply_during_animation_retains_quotes_and_resets(widget):
    widget._price_ready("BTCUSDT", Decimal("100"), "")
    widget.start_slide()
    assert widget.current_index == 1
    widget.apply_settings({**widget.config, "symbol3": "HYPEUSDT"})
    assert widget.current_index == 0
    assert widget.progress == 1
    assert widget.animation.state() == widget.animation.State.Stopped
    assert widget.quotes["BTCUSDT"].price == Decimal("100")
    assert "SOLUSDT" not in widget.quotes


def test_width_does_not_shrink_with_small_prices_or_rotation(widget, app):
    widget._price_ready("BTCUSDT", Decimal("123456789.12345678"), "")
    width = widget.width()
    widget._price_ready("BTCUSDT", Decimal("1"), "")
    widget.start_slide()
    QTest.qWait(320)
    assert widget.width() == width
    assert widget.progress == 1


def test_screen_clamping_and_shutdown(widget):
    widget.move(-99999, -99999)
    widget.ensure_visible()
    assert widget.screen().availableGeometry().contains(widget.frameGeometry())
    widget.shutdown()
    assert widget.client.closed
    assert not widget.update_timer.isActive()
    assert not widget.cycle_timer.isActive()


def test_reload_icons_feedback(widget):
    dialog = SettingsDialog(widget)
    dialog._refresh_icons()
    assert dialog.refresh_button.isEnabled()
    assert "3 个使用默认图标" in dialog.feedback.text()
    dialog.deleteLater()


def test_mini_switch_preserves_quotes_and_saved_preference(widget):
    widget._price_ready("BTCUSDT", Decimal("86517.30"), "")
    mini_area = widget.width() * widget.height()
    pending_cancellations = len(widget.client.cancelled)
    widget.set_mini_mode(False)
    assert widget.width() * widget.height() > mini_area * 2
    assert widget.store.load()["mini_mode"] is False
    widget.set_mini_mode(True)
    assert widget.width() * widget.height() == mini_area
    assert widget.quotes["BTCUSDT"].price == Decimal("86517.30")
    assert len(widget.client.cancelled) == pending_cancellations
    widget._price_ready("BTCUSDT", None, "网络请求失败")
    assert "保留上次价格" in widget.toolTip()
    assert "BTCUSDT" in widget.toolTip()


def test_transparent_mini_background_remains_mouse_hit_target():
    image = QImage(100, 28, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    draw_ticker(painter, QRectF(0, 0, 100, 28), Quote("BTCUSDT"), 2, 12, 0, mini=True)
    painter.end()
    assert image.pixelColor(45, 3).alpha() > 0


def test_mini_preview_cancel_and_full_font_restoration(widget):
    dialog = SettingsDialog(widget)
    assert not dialog.number_inputs["text_size"].isEnabled()
    assert dialog.number_inputs["text_size"].value() == 12
    dialog.mini_check.setChecked(False)
    assert dialog.preview.config["mini_mode"] is False
    assert dialog.number_inputs["text_size"].isEnabled()
    assert dialog.number_inputs["text_size"].value() == DEFAULT_CONFIG["text_size"]
    dialog.number_inputs["text_size"].setValue(32)
    dialog.mini_check.setChecked(True)
    assert dialog.number_inputs["text_size"].value() == 12
    dialog.mini_check.setChecked(False)
    assert dialog.number_inputs["text_size"].value() == 32
    dialog.reject()
    assert widget.config["mini_mode"] is True
    dialog._save()
    assert widget.store.load()["mini_mode"] is False
    dialog.deleteLater()


def test_hide_restore_preserves_settings_draft(widget, app):
    widget.show()
    widget.open_settings()
    dialog = widget.settings_dialog
    dialog.symbol_edits[0].setText("HYPEUSDT")
    widget.toggle_visibility()
    app.processEvents()
    assert not widget.isVisible()
    assert not dialog.isVisible()
    assert widget.update_timer.isActive()
    widget.toggle_visibility()
    assert widget.isVisible() and dialog.isVisible()
    assert dialog.symbol_edits[0].text() == "HYPEUSDT"
    dialog.reject()
    assert widget.settings_dialog is None


def test_hotkey_setting_validation_and_save(widget):
    dialog = SettingsDialog(widget)
    dialog.hotkey_edit.setText("Z")
    dialog._save()
    assert "快捷键" in dialog.feedback.text() or "搭配" in dialog.feedback.text()
    assert not widget.store.path.exists()
    dialog.hotkey_edit.setText("ctrl+shift+h")
    dialog._save()
    assert widget.config["hide_hotkey"] == "Ctrl+Shift+H"
    assert widget.store.load()["hide_hotkey"] == "Ctrl+Shift+H"
    dialog.deleteLater()


def test_source_selection_persists_and_old_quote_origin_remains_honest(widget):
    widget._price_ready("BTCUSDT", Decimal("100"), "", "binance")
    dialog = SettingsDialog(widget)
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("okx"))
    assert widget.client.source == "auto"
    dialog._save()
    assert widget.client.source == widget.store.load()["price_source"] == "okx"
    widget._price_ready("BTCUSDT", None, "OKX：网络请求失败", "")
    assert widget.quotes["BTCUSDT"].price == Decimal("100")
    assert widget.quotes["BTCUSDT"].source == "binance"
    assert "报价来源：Binance" in widget.toolTip()
    assert "当前模式：欧易（OKX）" in widget.toolTip()
    widget._price_ready("BTCUSDT", Decimal("101"), "", "okx")
    assert widget.quotes["BTCUSDT"].source == "okx"
    assert "报价来源：OKX" in widget.toolTip()
    dialog.deleteLater()


def test_click_refreshes_once_without_saving_position(widget, monkeypatch):
    requests = []
    monkeypatch.setattr(widget.client, "refresh_prices", lambda symbols: requests.append(symbols))
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    assert requests == [widget.symbols]
    assert not widget.store.path.exists()
    assert not widget.hold_timer.isActive()


def send_pointer(widget, kind, global_pos):
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, QPointF(widget.mapFromGlobal(global_pos)), QPointF(global_pos),
                        button, buttons, Qt.KeyboardModifier.NoModifier)
    from PyQt6.QtWidgets import QApplication
    QApplication.sendEvent(widget, event)


def test_hold_then_drag_saves_position_without_refresh(widget, monkeypatch):
    requests = []
    monkeypatch.setattr(widget.client, "refresh_prices", lambda symbols: requests.append(symbols))
    widget.move(100, 100)
    start = widget.mapToGlobal(QPoint(15, 14))
    send_pointer(widget, QEvent.Type.MouseButtonPress, start)
    send_pointer(widget, QEvent.Type.MouseMove, start + QPoint(20, 0))
    assert widget.pos() == QPoint(100, 100), "长按生效之前不能拖动"
    QTest.qWait(400)
    send_pointer(widget, QEvent.Type.MouseMove, start + QPoint(60, 20))
    send_pointer(widget, QEvent.Type.MouseButtonRelease, start + QPoint(60, 20))
    assert widget.pos() == QPoint(140, 120)
    saved = widget.store.load()
    assert (saved["pos_x"], saved["pos_y"]) == (140, 120)
    assert not requests


def test_fast_move_and_stationary_hold_do_not_refresh(widget, monkeypatch):
    requests = []
    monkeypatch.setattr(widget.client, "refresh_prices", lambda symbols: requests.append(symbols))
    start = widget.mapToGlobal(QPoint(15, 14))
    send_pointer(widget, QEvent.Type.MouseButtonPress, start)
    send_pointer(widget, QEvent.Type.MouseMove, start + QPoint(30, 0))
    send_pointer(widget, QEvent.Type.MouseButtonRelease, start + QPoint(30, 0))
    QTest.mousePress(widget, Qt.MouseButton.LeftButton)
    QTest.qWait(400)
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton)
    assert not requests
    assert not widget.store.path.exists()


def test_hide_and_shutdown_cancel_pending_hold(widget, app, monkeypatch):
    requests = []
    monkeypatch.setattr(widget.client, "refresh_prices", lambda symbols: requests.append(symbols))
    widget.show()
    QTest.mousePress(widget, Qt.MouseButton.LeftButton)
    widget.toggle_visibility()
    assert not widget.hold_timer.isActive()
    widget.toggle_visibility()
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton)
    assert not requests
    QTest.mousePress(widget, Qt.MouseButton.LeftButton)
    widget.shutdown()
    assert not widget.hold_timer.isActive()
    assert widget._press_pos is None


def test_compact_menu_actions_and_submenu(widget):
    menu = widget._create_context_menu()
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == [
        "迷你模式", "行情数据源", "设置", "打开交易工作台", "退出"]
    assert menu.font().pixelSize() == 11
    source_menu = menu.actions()[1].menu()
    assert source_menu.font().pixelSize() == 11
    source_menu.actions()[2].trigger()
    assert widget.config["price_source"] == "okx"
    menu.actions()[0].trigger()
    assert widget.config["mini_mode"] is False
    menu.deleteLater()


def test_startup_checkbox_cancel_enable_disable_and_failure(widget, monkeypatch):
    from coinpilot_ai.startup import StartupManager
    from test_startup import FakeRegistry
    monkeypatch.setattr("coinpilot_ai.startup.startup_command", lambda *_: "coinpilot-ai.exe")
    widget.startup = StartupManager(widget.store.path, widget.store.path.parent, registry=FakeRegistry())
    dialog = SettingsDialog(widget)
    assert not dialog.startup_check.isChecked()
    dialog.startup_check.setChecked(True)
    dialog.reject()
    assert widget.startup.read() is None
    dialog.deleteLater()

    dialog = SettingsDialog(widget)
    dialog.startup_check.setChecked(True)
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert widget.startup.read() == ("coinpilot-ai.exe", 1)
    dialog.deleteLater()

    dialog = SettingsDialog(widget)
    dialog.show()
    assert dialog.startup_check.isChecked()
    dialog.startup_check.setChecked(False)
    widget.startup.registry.fail_write = True
    dialog._save()
    assert dialog.isVisible()
    assert "开机启动项" in dialog.feedback.text()
    assert widget.startup.read() is not None
    widget.startup.registry.fail_write = False
    dialog._save()
    assert widget.startup.read() is None
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.deleteLater()


def test_startup_read_failure_allows_other_settings_to_save(widget):
    from coinpilot_ai.startup import StartupManager
    from test_startup import FakeRegistry
    registry = FakeRegistry()
    registry.fail_read = True
    widget.startup = StartupManager(widget.store.path, widget.store.path.parent, registry=registry)
    dialog = SettingsDialog(widget)
    assert not dialog.startup_check.isEnabled()
    assert "无法读取开机启动项" in dialog.feedback.text()
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert not registry.writes
    dialog.deleteLater()


def test_proxy_draft_validation_save_and_disable(widget):
    dialog = SettingsDialog(widget)
    assert dialog.proxy_check.isChecked()
    assert dialog.proxy_type_combo.currentData() == "socks5"
    assert dialog.proxy_host_edit.text() == "127.0.0.1"
    assert dialog.proxy_port_spin.value() == 7897
    dialog.proxy_host_edit.setText("socks5://127.0.0.1:7897")
    dialog._save()
    assert "代理主机地址无效" in dialog.feedback.text()
    assert not widget.store.path.exists()
    dialog.proxy_host_edit.setText(" localhost ")
    dialog.proxy_port_spin.setValue(1080)
    dialog.proxy_type_combo.setCurrentIndex(dialog.proxy_type_combo.findData("http"))
    assert widget.client.proxy_config["proxy_port"] == 7897
    dialog._save()
    assert widget.store.load()["proxy_host"] == "localhost"
    assert widget.client.proxy_config["proxy_port"] == 1080
    assert widget.client.proxy_config["proxy_type"] == "http"
    dialog.deleteLater()
    dialog = SettingsDialog(widget)
    dialog.proxy_check.setChecked(False)
    assert not dialog.proxy_host_edit.isEnabled()
    dialog.reject()
    assert widget.config["proxy_enabled"] is True
    dialog._save()
    assert widget.store.load()["proxy_enabled"] is False
    assert widget.client.proxy_config["proxy_enabled"] is False
    dialog.deleteLater()
