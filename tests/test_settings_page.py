"""统一设置入口、页内草稿和更新生命周期的离线回归测试。"""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from coinpilot_ai.config import DEFAULT_CONFIG, SettingsStore
from coinpilot_ai.widget import CoinPilotWidget
from coinpilot_ai.update_ui import UpdateController
from coinpilot_ai.cockpit import desktop
from coinpilot_ai.cockpit.service import CockpitService


class EmptyVault:
    def read(self, key):
        return None


@pytest.fixture
def unified(app, tmp_path, monkeypatch):
    config = dict(DEFAULT_CONFIG, proxy_enabled=False)
    service = CockpitService(config, tmp_path/'test.db', tmp_path/'icons', vault=EmptyVault(), autostart=False)
    service.fetch_candles = lambda *_: None
    service.refresh_market = lambda: None
    monkeypatch.setattr(desktop, 'CockpitService', lambda *a, **kw: service)
    widget = CoinPilotWidget(config, SettingsStore(tmp_path/'config.json'), start_requests=False)
    widget.reload_icons = lambda **kw: None
    widget.update_prices = lambda: None
    updater = UpdateController(app, widget, tmp_path/'icons', automatic=False)
    updater.client.check = lambda: None
    controller = desktop.DesktopController(app, widget, widget.store.path, tmp_path/'icons', updater=updater)
    controller.open_workbench()
    app.processEvents()
    yield controller, widget, updater
    updater.close()
    controller.close()
    widget.close()
    controller.deleteLater()
    updater.deleteLater()
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_all_entrypoints_select_existing_settings_page(unified, app):
    controller, widget, updater = unified
    assert all(len(action.text()) <= 4 for action in controller.menu.actions() if not action.isSeparator())
    window = controller.window
    page = window.settings_page
    assert set(page.section_indices) == {'常规与网络', '图表', '工作区布局', 'OKX 账户', 'AI 服务', '提示词模板', '提醒规则', '桌面与通知', '软件更新'}
    widget.open_settings()
    assert window.pages.currentWidget() is page
    assert page.tabs.currentWidget() is page.preferences
    assert widget.settings_dialog is None and not page.preferences.isWindow()
    window.chart.ema_settings()
    assert page.tabs.currentIndex() == page.section_indices['图表']
    assert not page.ema_editor.isWindow()
    updater.open()
    assert page.tabs.currentWidget() is page.update_panel
    assert updater.dialog is page.update_panel and not updater.dialog.isWindow()
    QTest.keyClick(page.update_panel, Qt.Key.Key_Escape)
    assert page.update_panel.isVisible()
    window.close()
    controller.settings_action.trigger()
    assert controller.window is window and window.isVisible()
    assert window.pages.currentWidget() is page
    menu = widget._create_context_menu()
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == ['设置', '交易台', '退出']
    menu.deleteLater()


def test_preferences_save_discard_and_navigation_preserve_drafts(unified, app):
    controller, widget, _ = unified
    window = controller.window
    page, editor = window.settings_page, window.settings_page.preferences
    widget.open_settings()
    editor.symbol_edits[0].setText('HYPEUSDT')
    editor.proxy_port_spin.setValue(1080)
    page.provider_name.setText('未保存的模型草稿')
    page.ai_key.setText('temporary-test-key')
    page.template_body.setPlainText('未保存的提示词')
    window.pages.setCurrentIndex(0)
    widget.open_settings()
    assert editor.symbol_edits[0].text() == 'HYPEUSDT'
    assert widget.config['symbol1'] == DEFAULT_CONFIG['symbol1']
    assert page.provider_name.text() == '未保存的模型草稿'
    assert page.ai_key.text() == 'temporary-test-key'
    assert page.template_body.toPlainText() == '未保存的提示词'
    editor._save()
    assert widget.config['symbol1'] == 'HYPEUSDT' and widget.config['proxy_port'] == 1080
    assert editor.isVisible() and window.pages.currentWidget() is page
    editor.symbol_edits[0].setText('SOLUSDT')
    editor.proxy_port_spin.setValue(8080)
    editor.reject()
    assert editor.symbol_edits[0].text() == 'HYPEUSDT' and editor.proxy_port_spin.value() == 1080
    assert editor.isVisible()


def test_ema_draft_and_magnet_are_shared_with_chart(unified):
    controller, _, _ = unified
    window = controller.window
    page = window.settings_page
    window.chart.ema_settings()
    period = page.ema_editor.table.cellWidget(0, 1)
    before = controller.service.chart_book.emas[0]['period']
    period.setValue(35)
    assert controller.service.chart_book.emas[0]['period'] == before
    page.ema_editor.reject()
    assert page.ema_editor.table.cellWidget(0, 1).value() == before
    page.ema_editor.table.cellWidget(0, 1).setValue(35)
    page.ema_editor.accept()
    assert controller.service.chart_book.emas[0]['period'] == 35
    assert page.ema_editor.isVisible()
    page.magnet.setChecked(False)
    assert not window.chart.canvas.magnet_enabled
    window.chart.magnet_button.setChecked(True)
    assert page.magnet.isChecked()


def test_update_install_failure_reenables_workbench(unified, tmp_path, monkeypatch):
    from coinpilot_ai import update_ui
    controller, _, updater = unified
    updater.open()
    updater.install_supported = True
    updater.trade_busy = lambda: False
    updater.client.release = SimpleNamespace(size=100, version='99.0.0', notes='测试发行版')
    updater.client._state('ready', '离线测试')
    ready, error = tmp_path/'ready', tmp_path/'error'
    monkeypatch.setattr(update_ui, 'launch_installer_helper', lambda *_: (SimpleNamespace(poll=lambda: None), ready, error))
    updater.prepare_install()
    assert not controller.window.isEnabled()
    error.write_text('测试助手失败', encoding='utf-8')
    updater.poll_helper()
    assert controller.window.isEnabled()
    assert updater.client.state == 'ready'
    assert updater.dialog is controller.window.settings_page.update_panel


def test_layout_preferences_save_while_workspace_is_hidden(unified):
    controller, _, _ = unified
    window = controller.window
    window.open_settings('工作区布局')
    window.workspace.set_visible('ai', False)
    window.workspace.set_locked(True)
    for _ in range(30):
        QTest.qWait(50)
        record = controller.service.store.get('workspace_layout', 'main', {})
        if record.get('locked'):
            break
    assert record['locked'] and not record['visible']['ai']
    assert window.pages.currentWidget() is window.settings_page
