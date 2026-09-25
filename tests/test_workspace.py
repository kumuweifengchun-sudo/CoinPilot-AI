"""使用临时数据验证停靠布局与交易表单的生命周期，不连接账户。"""
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QCheckBox, QDockWidget, QLabel, QMenuBar

from coinpilot_ai.config import DEFAULT_CONFIG
from coinpilot_ai.cockpit.chart_panel import ChartPanel
from coinpilot_ai.cockpit.service import CockpitService
from coinpilot_ai.cockpit.ui_ai import AiPanel
from coinpilot_ai.cockpit.workbench import Workbench


class EmptyVault:
    def read(self, _key):
        return None


@pytest.fixture
def workspace(app, tmp_path):
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path/'workspace.db',
                             tmp_path/'icons', vault=EmptyVault(), autostart=False)
    service.refresh_market = lambda: None
    service.fetch_candles = lambda *_: None
    service.ask_ai = lambda *_: pytest.fail('布局操作不应发起 AI 请求')
    window = Workbench(service, lambda: None)
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    yield window, service
    window.exiting = True
    window.close()
    window.deleteLater()
    service.close()
    service.deleteLater()
    app.processEvents()


def test_single_chart_placeholder_and_default_geometry(workspace, app):
    window, service = workspace
    docks = window.workspace.docks
    assert len(window.findChildren(ChartPanel)) == 1
    assert window.findChildren(AiPanel) == [window.review.ai_panel]
    assert window.ai_placeholder.text() == 'AI 功能规划中'
    assert window.pages.count() == 3
    assert docks['ai'].x() < docks['market'].x() < docks['order'].x()
    assert docks['info'].y() > docks['market'].y()
    assert docks['market'].width() > docks['ai'].width()
    assert docks['market'].height() > docks['info'].height()*2
    assert window.chart.trading


def test_title_tracks_environment_without_duplicate_branding(workspace, app):
    window, service = workspace
    assert window.windowTitle() == 'CoinPilot AI · 模拟环境'
    assert app.applicationName() == 'CoinPilot AI'
    assert app.applicationDisplayName() == ''
    assert not window.findChildren(QMenuBar)
    assert not window.findChild(QLabel, 'brandMark')
    for environment, label in (('live', '真实环境'), ('demo', '模拟环境')):
        service.change_account(environment)
        assert window.windowTitle() == f'CoinPilot AI · {label}'
        assert label in window.workspace.docks['order'].windowTitle()


def test_settings_restore_closed_panels_without_view_menu(workspace, app):
    window, _ = workspace
    window.workspace.docks['ai'].close()
    window.open_settings('工作区布局')
    check = next(c for c in window.settings_page.findChildren(QCheckBox) if c.text() == 'AI')
    assert not check.isChecked()
    check.setChecked(True)
    window.pages.setCurrentIndex(0)
    app.processEvents()
    assert window.workspace.docks['ai'].isVisible()


def test_titlebar_navigation_and_view_menu_share_existing_panels(workspace, app):
    window, _ = workspace
    bar = window.title_bar
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.pages.tabBar().isHidden()
    chart = window.chart
    window.trade.size.setText('123')
    for index in (1, 2, 0):
        bar.navigation[index].click()
        assert window.pages.currentIndex() == index
        assert bar.navigation[index].isChecked()
        assert window.chart is chart and window.trade.size.text() == '123'
    window.open_settings('工作区布局')
    assert bar.navigation[2].isChecked()
    action = window.workspace.actions['ai']
    assert action in bar.view_menu.actions()
    action.trigger()
    window.pages.setCurrentIndex(0)
    assert not window.workspace.docks['ai'].isVisible()
    action.trigger()
    assert window.workspace.docks['ai'].isVisible()
    assert bar.caption.text() == window.windowTitle()
    for button in (*bar.navigation, bar.view_button, bar.close_button):
        point = button.mapTo(bar, button.rect().center())
        assert not bar.is_drag_region(point)
    assert bar.is_drag_region(bar.caption.mapTo(bar, bar.caption.rect().center()))


def test_titlebar_window_controls_keep_background_service(workspace, app):
    window, service = workspace
    bar = window.title_bar
    original = window.size()
    bar.maximize_button.click()
    app.processEvents()
    assert window.isMaximized() and bar.maximize_button.toolTip() == '还原'
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(500, 20))
    app.processEvents()
    assert not window.isMaximized() and window.size() == original
    bar.minimize_button.click()
    assert window.isMinimized()
    window.showNormal()
    bar.close_button.click()
    assert not window.isVisible() and not service.closed
    window.show()
    assert window.isVisible() and window.workspace.docks['market'].isVisible()


def test_float_hide_reopen_and_pages_preserve_instances_and_draft(workspace, app):
    window, service = workspace
    layout = window.workspace
    window.trade.size.setText('123.45')
    chart = window.chart
    for dock in layout.docks.values():
        dock.setFloating(True)
    app.processEvents()
    layout.set_visible('ai', False)
    for _ in range(3):
        window.pages.setCurrentIndex(1)
        app.processEvents()
        assert all(not d.isVisible() for d in layout.docks.values())
        window.pages.setCurrentIndex(0)
        app.processEvents()
        assert layout.docks['market'].isVisible()
        assert not layout.docks['ai'].isVisible()
    window.close()
    assert not service.closed
    assert all(not d.isVisible() for d in layout.docks.values())
    window.show()
    app.processEvents()
    assert window.chart is chart and len(window.findChildren(ChartPanel)) == 1
    assert window.trade.size.text() == '123.45'
    assert layout.docks['order'].isVisible() and layout.docks['order'].isFloating()
    assert not layout.docks['ai'].isVisible()


def test_layout_roundtrip_tabification_lock_and_hidden_panel(workspace, app):
    window, service = workspace
    layout = window.workspace
    layout.host.tabifyDockWidget(layout.docks['ai'], layout.docks['info'])
    layout.docks['order'].setFloating(True)
    layout.docks['order'].resize(360, 620)
    layout.set_visible('market', False)
    layout.set_locked(True)
    app.processEvents()
    layout.save()
    restored = Workbench(service, lambda: None)
    try:
        restored.show()
        app.processEvents()
        other = restored.workspace
        assert other.locked
        assert other.docks['order'].isFloating()
        assert other.docks['info'] in other.host.tabifiedDockWidgets(other.docks['ai'])
        assert not other.docks['market'].isVisible()
        assert not other.docks['ai'].features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
        other.reset()
        app.processEvents()
        assert all(d.isVisible() and not d.isFloating() for d in other.docks.values())
        assert not other.locked
    finally:
        restored.exiting = True
        restored.close()
        restored.deleteLater()
        app.processEvents()


def test_page_switch_keeps_dock_sizes_and_maximize_is_temporary(workspace, app):
    window, service = workspace
    layout = window.workspace
    QTest.qWait(30)
    widths = {k: d.width() for k, d in layout.docks.items()}
    for _ in range(3):
        window.pages.setCurrentIndex(1)
        window.pages.setCurrentIndex(0)
        app.processEvents()
    assert all(abs(d.width()-widths[k]) <= 2 for k, d in layout.docks.items()), (widths, {k: d.width() for k, d in layout.docks.items()})
    layout.set_visible('ai', False)
    window.chart.maximize()
    app.processEvents()
    assert not layout.docks['order'].isVisible()
    layout.save()
    assert service.store.get('workspace_layout', 'main')['visible']['order']
    window.close()
    window.show()
    app.processEvents()
    assert not layout.docks['order'].isVisible()
    window.chart.maximize()
    app.processEvents()
    assert layout.docks['order'].isVisible() and not layout.docks['ai'].isVisible()


def test_close_single_panel_actions_and_coalesced_save(workspace, app, monkeypatch):
    window, service = workspace
    layout = window.workspace
    QTest.qWait(50)
    layout.timer.stop()
    writes = []
    original = service.store.put
    monkeypatch.setattr(service.store, 'put', lambda *args, **kw: (writes.append(args), original(*args, **kw))[-1])
    for _ in range(20):
        layout.schedule()
    assert not writes
    for _ in range(30):
        if any(w[0] == 'workspace_layout' for w in writes):
            break
        QTest.qWait(50)
    assert len([w for w in writes if w[0] == 'workspace_layout']) == 1
    layout.docks['order'].close()
    assert not layout.desired['order'] and not layout.actions['order'].isChecked()
    layout.actions['order'].trigger()
    assert layout.docks['order'].isVisible()
    for index, visible in ((0, [True, True, False, False]), (1, [False, False, True, False]),
                           (3, [False, False, False, True]), (5, [False]*4)):
        window.trade.tabs.setCurrentIndex(index)
        assert [b.isVisible() for b in window.trade.action_buttons] == visible
    layout.set_visible('order', False)
    window.trade.tabs.setCurrentIndex(1)
    window.trade.cancel_order()
    assert window.trade.operation_feedback.isVisible()
    assert '选择要撤销' in window.trade.operation_feedback.text()


@pytest.mark.parametrize('record', [None, {'version': 99}, {'version': 1, 'state': 'not base64'},
    {'version': 1, 'visible': ['ai', 'market', 'info', 'order']},
    {'version': 1, 'visible': dict.fromkeys(('ai', 'market', 'info', 'order'), True), 'state': '!!!!', 'geometry': ''}])
def test_invalid_layout_recovers_without_touching_chart_data(workspace, app, record):
    window, service = workspace
    service.store.put('workspace_layout', 'main', record)
    other = Workbench(service, lambda: None)
    try:
        other.show()
        app.processEvents()
        assert all(other.workspace.desired.values())
        assert len(other.findChildren(ChartPanel)) == 1
    finally:
        other.exiting = True
        other.close()
        other.deleteLater()
        app.processEvents()


def test_offscreen_floating_window_returns_to_available_screen(workspace, app):
    window, _ = workspace
    dock = window.workspace.docks['order']
    dock.setFloating(True)
    dock.move(-30000, -30000)
    window.workspace.recover_screens()
    app.processEvents()
    assert any(s.availableGeometry().contains(dock.frameGeometry().topLeft()) for s in app.screens())
    dock.move(-30000, -30000)
    window.close()
    window.show()
    app.processEvents()
    assert any(s.availableGeometry().contains(dock.frameGeometry().topLeft()) for s in app.screens())
