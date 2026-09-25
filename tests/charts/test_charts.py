"""图表交互、历史合并、持久化和无下单权限的回归测试。"""
from copy import deepcopy
import json
import statistics
import time

import pytest
from PyQt6.QtCore import QObject, QPoint, QPointF, Qt, pyqtSignal
from PyQt6.QtNetwork import QNetworkProxy
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QMenu

from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.charts.canvas import CandleChart
from coinpilot_ai.charts.dialogs import DrawingDialog, EmaDialog
from coinpilot_ai.market.candles import CandleStream, valid_candles
from coinpilot_ai.charts.state import OBJECT_NAMES, ChartBook, drawing, ema_values, trade_lines
from coinpilot_ai.market.series import ChartSeries
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.core.store import Store
from coinpilot_ai.integrations.transport import ApiError
from coinpilot_ai.workbench.window import Workbench


class EmptyVault:
    def read(self, key):
        return None


def candles(count=300, begin=1700000000000, interval=900000):
    return [[str(begin+i*interval), str(100+i*.01), str(102+i*.01), str(98+i*.01), str(101+i*.01), str(10+i%17), "0", "0", "1"] for i in range(count)]


def test_shared_indicator_result_is_reused_for_same_pair_and_revision(service):
    pair = ("BTC-USDT-SWAP", "15m")
    service.candles[pair] = candles(30)
    series = service.chart_feed.get_series(pair)
    spec = {"kind": "RSI", "params": {"period": 14}}
    first = service.chart_feed.indicator(series, spec)
    assert service.chart_feed.indicator(series, spec) is first
    service.chart_feed.merge(pair, candles(31))
    second = service.chart_feed.indicator(series, spec)
    assert second is not first and len(second.times) == 31


def test_cross_period_indicator_waits_for_source_bar_close(service):
    inst = "BTC-USDT-SWAP"
    begin = 1700000000000
    source = candles(2, begin, 3600000)
    source[0][4] = "100"
    source[1][4] = "200"
    source[1][2] = "201"
    service.candles[(inst, "1H")] = source
    canvas = CandleChart(service)
    canvas.set_context("demo", inst, "15m")
    service.chart_book.save_indicators([{"id": "hourly", "kind": "MA", "params": {"period": 1},
        "bar": "1H", "color": "#abcdef", "width": 1.5, "visible": True}])
    canvas.set_data(candles(8, begin, 900000))
    values = canvas.indicator_results[0][1].lines["value"]
    assert values[:3] == (None, None, None)
    assert values[3:7] == (100.0, 100.0, 100.0, 100.0)
    assert values[7] == 200.0
    canvas.deleteLater()


@pytest.fixture
def service(app, tmp_path):
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path/"charts.db", tmp_path/"cache", vault=EmptyVault(), autostart=False)
    service.refresh_market = lambda: None
    yield service
    service.close()
    service.deleteLater()
    app.processEvents()


@pytest.fixture
def chart(app, service):
    result = CandleChart(service)
    result.resize(950, 530)
    result.set_context("demo", "BTC-USDT-SWAP", "15m")
    result.set_data(candles())
    result.show()
    app.processEvents()
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def drag(chart, start, end):
    QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=QPoint(*start))
    move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(*end), QPointF(*end), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    chart.mouseMoveEvent(move)
    QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=QPoint(*end))


def test_pan_both_axes_and_new_quotes_keep_manual_view(chart):
    left, low, high = chart.left_time, chart.low, chart.high
    drag(chart, (430, 170), (510, 215))
    assert chart.left_time < left
    assert chart.low > low and chart.high > high
    assert not chart.follow and not chart.auto_scale
    view = chart.left_time, chart.low, chart.high
    chart.set_data(candles(301))
    chart.repaint()
    assert (chart.left_time, chart.low, chart.high) == view
    chart.auto()
    chart.latest()
    assert chart.follow and chart.auto_scale


def test_time_zoom_anchors_pointer_and_price_axis_reset(chart):
    main, volume = chart.plot_rects()
    pos = QPointF(440, 200)
    before = chart.anchor(pos)[0]
    # 缩放保持光标所在的时间位置，而不是可见区域中心。
    old_index = chart.left_index()+(pos.x()-main.left())/main.width()*chart.count
    fixed = chart.time_at(old_index)
    chart.zoom_time(.7, pos.x())
    new_index = chart.left_index()+(pos.x()-main.left())/main.width()*chart.count
    assert chart.time_at(new_index) == pytest.approx(fixed)
    span = chart.high-chart.low
    x = int(main.right()+35)
    drag(chart, (x, 170), (x, 230))
    assert chart.high-chart.low > span and not chart.auto_scale
    QTest.mouseDClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(x, 170))
    assert chart.auto_scale
    count = chart.count
    drag(chart, (400, int(volume.bottom()+12)), (470, int(volume.bottom()+12)))
    assert chart.count < count


def test_history_prepend_and_resize_keep_time_price_anchors(chart):
    chart.follow = False
    chart.auto_scale = False
    chart.objects = [drawing("trend", [[chart.times[-70], 102], [chart.times[-30], 104]])]
    before = deepcopy(chart.objects)
    screen = chart.point(before[0]["anchors"][0])
    left = chart.left_time
    chart.set_data(candles(600, begin=1700000000000-300*900000))
    assert chart.left_time == left
    assert chart.point(before[0]["anchors"][0]).x() == pytest.approx(screen.x())
    chart.resize(1200, 720)
    assert chart.objects == before
    assert chart.anchor(chart.point(before[0]["anchors"][0])) == pytest.approx(before[0]["anchors"][0])


@pytest.mark.parametrize("tool", ["trend", "horizontal", "ray", "vertical", "rectangle", "fib", "measure", "text"])
def test_tools_create_edit_lock_delete_undo_redo(chart, app, monkeypatch, tool):
    from coinpilot_ai.charts import canvas as module
    monkeypatch.setattr(module.QInputDialog, "getText", lambda *a: ("观察支撑", True))
    chart.set_tool(tool)
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(240, 180))
    if tool not in ("horizontal", "vertical", "text"):
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(500, 240))
    assert len(chart.objects) == 1
    first = deepcopy(chart.objects[0])
    assert chart.tool == tool and chart.preview is None
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(340, 180))
    if tool not in ('horizontal', 'vertical', 'text'):
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(550, 275))
    if chart.draw_click_timer.isActive():
        QTest.qWait(app.doubleClickInterval()+60)
    assert len(chart.objects) == 2 and chart.objects[0] == first
    assert chart.objects[1]['id'] != first['id']
    QTest.keyClick(chart, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert chart.objects == [first] and chart.tool == tool
    chart.repaint()
    obj = chart.objects[0]
    before = deepcopy(obj["anchors"])
    chart.set_tool('cursor')
    chart.selected_id = obj['id']
    anchor = chart.point(obj["anchors"][0])
    drag(chart, (int(anchor.x()), int(anchor.y())), (int(anchor.x()+25), int(anchor.y()+15)))
    assert chart.objects[0]["anchors"] != before
    chart.objects[0]["locked"] = True
    chart.persist_objects()
    chart.delete_selected()
    assert len(chart.objects) == 1
    chart.objects[0]["locked"] = False
    chart.persist_objects()
    chart.delete_selected()
    assert not chart.objects
    chart.book.undo(chart.environment, chart.instrument)
    assert len(chart.objects) == 1
    chart.book.undo(chart.environment, chart.instrument, redo=True)
    assert not chart.objects


def test_cancel_drawing_and_move_do_not_write_partial_changes(chart):
    chart.set_tool("trend")
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(240, 180))
    assert chart.preview is not None
    QTest.keyClick(chart, Qt.Key.Key_Escape)
    assert chart.preview is None and not chart.objects
    assert not chart.book.objects("demo", chart.instrument)


def test_objects_multiselect_delete_skips_locked_and_undo_restores_batch(chart, app):
    from coinpilot_ai.charts.dialogs import ObjectsDialog
    objects = [drawing('trend', [[chart.times[-70], 100+i], [chart.times[-20], 102+i]]) for i in range(5)]
    objects[-1]['locked'] = True
    chart.objects = deepcopy(objects)
    chart.persist_objects()
    objects = deepcopy(chart.objects)
    dialog = ObjectsDialog(chart)
    dialog.show()
    app.processEvents()
    try:
        def select(row, modifiers=Qt.KeyboardModifier.NoModifier):
            QTest.mouseClick(dialog.items.viewport(), Qt.MouseButton.LeftButton, modifiers,
                             dialog.items.visualItemRect(dialog.items.item(row)).center())
        select(0)
        select(2, Qt.KeyboardModifier.ControlModifier)
        assert len(dialog.identities()) == 2
        select(4, Qt.KeyboardModifier.ShiftModifier)
        assert dialog.identities() == {objects[i]['id'] for i in (0, 2, 3, 4)}
        assert not dialog.edit_button.isEnabled()
        QTest.keyClick(dialog.items, Qt.Key.Key_Delete)
        remaining = [objects[1], objects[4]]
        assert chart.objects == remaining
        assert ChartBook(chart.service.store).objects('demo', chart.instrument) == remaining
        assert '保留 1 个锁定对象' in dialog.feedback.text()
        assert dialog.identities() == {objects[4]['id']}
        # 只剩锁定项时不产生空撤销记录；一次撤销恢复整批。
        dialog.delete()
        chart.book.undo('demo', chart.instrument)
        assert chart.objects == objects
        chart.book.undo('demo', chart.instrument, redo=True)
        assert chart.objects == remaining
        dialog.reload()
        QTest.keyClick(dialog.items, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert len(dialog.identities()) == 2
        dialog.toggle('locked')
        assert len(dialog.identities()) == 2 and all(o['locked'] for o in chart.objects)
        dialog.toggle('locked')
        assert all(not o['locked'] for o in chart.objects)
        dialog.delete_button.click()
        assert not chart.objects and not dialog.delete_button.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_shared_drawings_view_and_global_ema_survive_restart(chart, service, app):
    other = CandleChart(service)
    other.set_context("demo", chart.instrument, "15m")
    other.set_data(candles())
    obj = drawing("fib", [[chart.times[-60], 102], [chart.times[-20], 106]])
    chart.objects = [obj]
    chart.persist_objects()
    assert other.objects == chart.objects
    chart.zoom_time(.6, 400)
    chart.flush_view()
    assert other.count == chart.count and other.left_time == chart.left_time
    other.set_context("demo", chart.instrument, "1H")
    assert other.objects == [obj]
    other.set_context("live", chart.instrument, "1H")
    assert other.objects == []
    service.chart_book.save_emas([{"period": 9, "color": "#ffffff", "width": 3, "visible": True}])
    assert chart.emas == other.emas
    reloaded = ChartBook(service.store)
    assert reloaded.objects("demo", chart.instrument) == [obj]
    assert reloaded.view("demo", chart.instrument, "15m")["count"] == chart.count
    assert reloaded.emas[0]["period"] == 9
    other.deleteLater()
    app.processEvents()


def test_drawing_names_migrate_and_reuse_numbers_after_deletion_and_restart(service):
    legacy = [drawing(tool, [[1700000000000, 100], [1700000900000, 101]])
              for tool in ('trend', 'trend', 'horizontal')]
    inst = service.selected
    service.store.put('chart_drawings', inst, legacy, 'demo')
    service.store.put('chart_drawing_names', inst, {'trend': 99, 'horizontal': 20}, 'demo')
    book = ChartBook(service.store)
    rows = book.objects('demo', inst)
    assert [o['name'] for o in rows] == ['趋势线1', '趋势线2', '水平线1']
    assert [o['anchors'] for o in rows] == [o['anchors'] for o in legacy]
    rows[0]['name'] = '日线支撑'
    book.save_objects('demo', inst, rows)
    book.save_objects('demo', inst, [])
    reopened = ChartBook(service.store)
    created = [drawing('trend', legacy[0]['anchors'])]
    reopened.save_objects('demo', inst, created)
    assert created[0]['name'] == '趋势线1'
    assert ChartBook(service.store).objects('demo', inst) == created
    for environment, instrument in (('live', inst), ('demo', 'ETH-USDT-SWAP')):
        fresh = [drawing('trend', legacy[0]['anchors'])]
        reopened.save_objects(environment, instrument, fresh)
        assert fresh[0]['name'] == '趋势线1'
    collision = [dict(created[0], name='趋势线8'), drawing('trend', legacy[0]['anchors'])]
    reopened.save_objects('demo', inst, collision)
    assert [o['name'] for o in collision] == ['趋势线8', '趋势线1']


@pytest.mark.parametrize('tool', [tool for tool in OBJECT_NAMES if tool != 'cursor'])
def test_drawing_names_reuse_gaps_and_preserve_undo_redo(service, tool):
    book, inst = service.chart_book, service.selected
    anchors = [[1700000000000, 100], [1700000900000, 101]]
    rows = [drawing(tool, anchors) for _ in range(3)]
    book.save_objects('demo', inst, rows)
    original = deepcopy(rows)
    # 隐藏、锁定的对象仍占用名称；删除中间对象后不重排现有名称。
    rows = [dict(rows[0], hidden=True), dict(rows[2], locked=True)]
    book.save_objects('demo', inst, rows)
    book.undo('demo', inst)
    assert book.objects('demo', inst) == original
    book.undo('demo', inst, redo=True)
    assert book.objects('demo', inst) == rows
    remaining = deepcopy(rows)
    rows.extend(drawing(tool, anchors) for _ in range(2))
    book.save_objects('demo', inst, rows)
    prefix = OBJECT_NAMES[tool]
    assert [o['name'] for o in rows] == [prefix+str(i) for i in (1, 3, 2, 4)]
    book.undo('demo', inst)
    assert book.objects('demo', inst) == remaining
    book.undo('demo', inst, redo=True)
    assert book.objects('demo', inst) == rows
    assert ChartBook(service.store).objects('demo', inst) == rows
    rows[0]['name'] = '自定义名称'
    book.save_objects('demo', inst, rows)
    rows.append(drawing(tool, anchors))
    book.save_objects('demo', inst, rows)
    assert rows[0]['name'] == '自定义名称'
    assert rows[-1]['name'] == prefix+'1'


def test_object_rename_persistence_undo_and_empty_name(chart, monkeypatch):
    from coinpilot_ai.charts import dialogs as module
    chart.objects = [drawing('trend', [[chart.times[-60]+.123, 100.1234567890123], [chart.times[-20], 102]])]
    chart.persist_objects()
    original = deepcopy(chart.objects[0])
    def save(dialog):
        assert dialog.name.text() == '趋势线1'
        dialog.name.setText('  日线压力线  ')
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(module.DrawingDialog, 'exec', save)
    module.edit_drawing(chart, original['id'], chart)
    assert chart.objects[0]['name'] == '日线压力线'
    assert chart.objects[0]['id'] == original['id']
    assert chart.objects[0]['anchors'] == original['anchors']
    assert ChartBook(chart.service.store).objects('demo', chart.instrument)[0]['name'] == '日线压力线'
    manager = module.ObjectsDialog(chart)
    assert manager.items.item(0).text().startswith('日线压力线')
    manager.deleteLater()
    chart.book.undo('demo', chart.instrument)
    assert chart.objects[0]['name'] == '趋势线1'
    chart.book.undo('demo', chart.instrument, redo=True)
    assert chart.objects[0]['name'] == '日线压力线'
    dialog = module.DrawingDialog(chart.objects[0])
    dialog.name.setText('  ')
    dialog.accept()
    assert not dialog.result() and '名称' in dialog.error.text()
    dialog.reject()
    assert chart.objects[0]['name'] == '日线压力线'
    dialog.deleteLater()


def test_ema_numerical_values_and_view_independence(chart):
    assert ema_values([1, 2, 3, 4], 3) == [1, 1.5, 2.25, 3.125]
    before = deepcopy(chart.ema_cache)
    chart.zoom_time(1.4, 300)
    chart.latest()
    chart.repaint()
    assert chart.ema_cache == before


def test_region_fill_render_undo_and_restart(chart, service, app, monkeypatch):
    from PyQt6.QtGui import QColor
    from coinpilot_ai.charts.canvas import QColorDialog
    corners = [QPointF(260, 170), QPointF(560, 170), QPointF(410, 310)]
    chart.objects = [drawing('trend', [chart.anchor(a), chart.anchor(b)])
                     for a, b in zip(corners, corners[1:]+corners[:1])]
    chart.persist_objects()
    point = QPointF(410, 230)
    anchors = chart.region_at(point)
    assert len(anchors) == 3
    before = chart.grab().toImage().pixelColor(point.toPoint())
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *_: QColor('#ff0000'))
    chart.fill_region(anchors)
    obj = chart.objects[-1]
    assert obj['tool'] == 'region' and obj['fill_opacity'] == 20
    assert chart.grab().toImage().pixelColor(point.toPoint()) != before
    assert chart.hit_test(point)[0] == obj['id']
    chart.zoom_time(.8, 410)
    chart.repaint()
    assert chart.objects[-1]['anchors'] == anchors
    reloaded = ChartBook(service.store)
    assert reloaded.objects('demo', chart.instrument)[-1] == obj
    chart.book.undo('demo', chart.instrument)
    assert len(chart.objects) == 3
    chart.book.undo('demo', chart.instrument, redo=True)
    assert chart.objects[-1] == obj
    chart.selected_id = obj['id']
    chart.delete_selected()
    assert len(chart.objects) == 3
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *_: QColor())
    chart.fill_region(anchors)
    assert len(chart.objects) == 3
    chart.fill_region(anchors, ('live', chart.instrument, chart.bar))
    assert len(chart.objects) == 3


def test_fill_properties_and_lock(chart):
    obj = drawing('rectangle', [chart.anchor(QPointF(200, 150)), chart.anchor(QPointF(400, 300))])
    dialog = DrawingDialog(obj)
    dialog.fill_color.set_color('#123456')
    dialog.fill_opacity.setValue(45)
    dialog.accept()
    assert dialog.result_object['fill_color'] == '#123456'
    assert dialog.result_object['fill_opacity'] == 45
    locked = dict(dialog.result_object, locked=True)
    dialog.deleteLater()
    dialog = DrawingDialog(locked)
    dialog.fill_opacity.setValue(90)
    dialog.accept()
    assert dialog.result_object['fill_opacity'] == 45
    dialog.deleteLater()


@pytest.mark.parametrize('tool, unfinished', [('cursor', False), ('trend', False),
                                            ('trend', True), ('horizontal', False), ('text', False)])
def test_double_click_edits_existing_drawing_while_tool_stays_active(chart, app, monkeypatch, tool, unfinished):
    from coinpilot_ai.charts.dialogs import edit_drawing
    chart.set_magnet(False)
    chart.objects = [drawing('trend', [chart.anchor(QPointF(240, 160)), chart.anchor(QPointF(500, 220))])]
    chart.persist_objects()
    original = deepcopy(chart.objects[0])
    chart.set_tool(tool)
    if unfinished:
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(180, 280))
        assert chart.preview is not None
    opened = []
    def edit(dialog):
        opened.append(dialog.name.text())
        assert chart.pending_draw is None and chart.preview is None and chart.drag is None
        dialog.name.setText('双击编辑的趋势线')
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(DrawingDialog, 'exec', edit)
    chart.edit_requested.connect(lambda identity: edit_drawing(chart, identity, chart))
    app.clipboard().setText('unchanged')
    # Qt 的真实双击先发送一次普通点击，再发送双击事件。
    position = QPoint(370, 190)
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(chart, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=position)
    assert opened == [original['name']]
    assert len(chart.objects) == 1 and chart.objects[0]['id'] == original['id']
    assert chart.objects[0]['name'] == '双击编辑的趋势线'
    for actual, expected in zip(chart.objects[0]['anchors'], original['anchors']):
        assert actual == pytest.approx(expected)
    assert chart.tool == tool and not chart.draw_click_timer.isActive()
    assert app.clipboard().text() == 'unchanged'
    chart.book.undo(chart.environment, chart.instrument)
    assert chart.objects[0]['name'] == original['name'] and len(chart.objects) == 1


def test_single_click_on_existing_drawing_still_starts_and_finishes_drawing(chart, app):
    chart.set_magnet(False)
    chart.objects = [drawing('trend', [chart.anchor(QPointF(240, 160)), chart.anchor(QPointF(500, 220))])]
    chart.persist_objects()
    chart.set_tool('trend')
    start = QPoint(370, 190)
    expected = chart.anchor(QPointF(start))
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=start)
    assert chart.pending_draw is not None and len(chart.objects) == 1
    QTest.qWait(app.doubleClickInterval()+60)
    assert chart.pending_draw is None and chart.preview['anchors'][0] == expected
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(550, 290))
    assert len(chart.objects) == 2 and chart.objects[-1]['anchors'][0] == expected
    assert chart.tool == 'trend'
    # 快速点击另一个落点时，先完成前一个待判定的单击。
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(620, 260))
    assert len(chart.objects) == 3 and chart.preview is None


@pytest.mark.parametrize('action', ['escape', 'context', 'hide'])
def test_pending_drawing_click_is_cancelled_when_leaving_interaction(chart, app, action):
    chart.objects = [drawing('horizontal', [chart.anchor(QPointF(370, 190))])]
    chart.persist_objects()
    chart.set_tool('horizontal')
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(370, 190))
    assert chart.pending_draw is not None
    if action == 'escape':
        QTest.keyClick(chart, Qt.Key.Key_Escape)
    elif action == 'context':
        chart.set_context('demo', 'ETH-USDT-SWAP', '1H')
    else:
        chart.hide()
    assert chart.pending_draw is None and not chart.draw_click_timer.isActive()
    assert len(chart.book.objects('demo', 'BTC-USDT-SWAP')) == 1


def test_menu_and_double_click_copy_clicked_price(chart, app, service):
    service.specs[chart.instrument] = {'tickSz': '0.1'}
    pos = QPointF(410, 230)
    expected = chart.price_at(pos)
    menu = chart.context_menu(pos)
    actions = {a.text(): a for a in menu.actions()}
    assert {'复制价格', '提醒…', '快捷下单', '封闭区域填色…'} <= actions.keys()
    assert not actions['封闭区域填色…'].isEnabled()
    actions['复制价格'].trigger()
    assert app.clipboard().text() == expected
    app.clipboard().setText('unchanged')
    QTest.mouseDClick(chart, Qt.MouseButton.LeftButton, pos=pos.toPoint())
    assert app.clipboard().text() == expected
    app.clipboard().setText('axis')
    QTest.mouseDClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(chart.width()-5, 200))
    assert app.clipboard().text() == 'axis'
    emitted = []
    chart.order_requested.connect(lambda *args: emitted.append(args))
    actions['快捷下单'].menu().actions()[1].trigger()
    assert emitted == [('demo', chart.instrument, expected, 'short')]
    assert not service.store.list('local_order', service.scope)
    menu.deleteLater()


def test_chart_shortcut_prefills_only_and_alert_cancel_environment_guard(service, app, monkeypatch):
    from coinpilot_ai.workbench import window as module
    service.fetch_candles = lambda *_: None
    window = Workbench(service)
    try:
        window.chart_order('demo', service.selected, '60000.1', 'short')
        assert window.trade.price.text() == '60000.1'
        assert window.trade.order_type.currentData() == 'limit'
        assert window.trade.action.currentData() == 'open:short'
        assert not service.store.list('local_order', service.scope)
        window.chart_order('live', service.selected, '7', 'long')
        assert window.trade.price.text() == '60000.1'
        service.quotes[service.selected] = {'price': '62000'}
        captured = []
        def cancel(dialog):
            assert window.pages.currentIndex() == 0
            assert dialog.parentWidget() is window.chart.canvas.window()
            assert dialog.windowTitle() == '价格提醒 · 模拟环境'
            captured.append(dialog)
            return 0
        monkeypatch.setattr(module.RuleDialog, 'exec', cancel)
        window.chart_alert('demo', service.selected, '60000.1')
        assert not service.rules()
        assert captured[-1].rows[0][2].currentData() == 'below'
        assert captured[-1].rows[0][3].text() == '60000.1'
        def save(dialog):
            assert window.pages.currentIndex() == 0
            dialog._accept()
            return 1
        monkeypatch.setattr(module.RuleDialog, 'exec', save)
        window.chart_alert('demo', service.selected, '63000')
        assert window.pages.currentIndex() == 0
        assert len(service.rules()) == 1
        assert service.rules()[0][1]['conditions'][0]['threshold'] == '63000'
        def switch(dialog):
            dialog._accept()
            service.generation += 1
            return 1
        monkeypatch.setattr(module.RuleDialog, 'exec', switch)
        window.chart_alert('demo', service.selected, '64000')
        assert len(service.rules()) == 1
    finally:
        window.exiting = True
        window.close()
        window.deleteLater()
        app.processEvents()


def move_pointer(chart, position, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QMouseEvent(QMouseEvent.Type.MouseMove, position, position, Qt.MouseButton.NoButton,
                       Qt.MouseButton.NoButton, modifiers)
    chart.mouseMoveEvent(event)


@pytest.mark.parametrize("tool", ["trend", "fib", "ray", "rectangle", "measure", "horizontal", "vertical", "text"])
def test_drawing_magnet_snaps_exact_wick_time_and_high_low(chart, monkeypatch, tool):
    from coinpilot_ai.charts import canvas as module
    monkeypatch.setattr(module.QInputDialog, "getText", lambda *a: ("低点", True))
    low = [chart.times[-70], chart.values[-70][2]]
    high = [chart.times[-20], chart.values[-20][1]]
    first, second = chart.point(low)+QPointF(2, -4), chart.point(high)+QPointF(-2, 4)
    chart.set_tool(tool)
    move_pointer(chart, first)
    assert chart.snap_target == (*low, "最低")
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=first.toPoint())
    if tool not in ("horizontal", "vertical", "text"):
        move_pointer(chart, second)
        assert chart.preview["anchors"] == [low, high]
        assert chart.snap_target == (*high, "最高")
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=second.toPoint())
        assert chart.objects[0]["anchors"] == [low, high]
    else:
        assert chart.objects[0]["anchors"] == [low]
    assert chart.book.objects(chart.environment, chart.instrument) == chart.objects
    assert chart.snap_target is None
    assert not chart.service.store.list("local_order", chart.service.scope)


def test_magnet_toggle_alt_and_distant_points_stay_free(chart):
    target = [chart.times[-50], chart.values[-50][1]]
    position = chart.point(target)+QPointF(2, 4)
    chart.set_tool("fib")
    chart.draw_click(chart.point([chart.times[-80], chart.values[-80][2]]))
    move_pointer(chart, position)
    assert chart.preview["anchors"][-1] == target
    QTest.keyPress(chart, Qt.Key.Key_Alt)
    assert chart.snap_target is None
    assert chart.preview["anchors"][-1] == pytest.approx(chart.anchor(position))
    QTest.keyRelease(chart, Qt.Key.Key_Alt)
    assert chart.preview["anchors"][-1] == target
    chart.set_magnet(False)
    assert chart.preview["anchors"][-1] == pytest.approx(chart.anchor(position))
    assert chart.snap_target is None
    chart.set_magnet(True)
    center = chart.point([chart.times[-50], sum(chart.values[-50][1:3])/2])
    assert chart.drawing_anchor(center) == pytest.approx(chart.anchor(center))
    assert chart.snap_target is None


def test_magnet_moves_only_selected_anchor_and_supports_undo(chart):
    before = [[chart.times[-80], 100.4], [chart.times[-20], 102.5]]
    obj = drawing("fib", before)
    chart.objects = [obj]
    chart.selected_id = obj["id"]
    chart.persist_objects()
    chart.repaint()
    target = [chart.times[-50], chart.values[-50][1]]
    start, end = chart.point(before[0]), chart.point(target)+QPointF(2, 4)
    drag(chart, (round(start.x()), round(start.y())), (round(end.x()), round(end.y())))
    assert chart.objects[0]["anchors"] == [target, before[1]]
    chart.book.undo(chart.environment, chart.instrument)
    assert chart.objects[0]["anchors"] == before
    chart.book.undo(chart.environment, chart.instrument, redo=True)
    assert chart.objects[0]["anchors"] == [target, before[1]]
    chart.objects[0]["locked"] = True
    chart.persist_objects()
    start = chart.point(target)
    drag(chart, (round(start.x()), round(start.y())), (400, 200))
    assert chart.objects[0]["anchors"] == [target, before[1]]


def test_magnet_does_not_distort_whole_object_translation(chart):
    before = [[chart.times[-80], 100.4], [chart.times[-20], 102.5]]
    obj = drawing("trend", before)
    chart.objects = [obj]
    chart.selected_id = obj["id"]
    chart.persist_objects()
    chart.repaint()
    start = (chart.point(before[0])+chart.point(before[1]))/2
    end = chart.point([chart.times[-45], chart.values[-45][1]])
    drag(chart, (round(start.x()), round(start.y())), (round(end.x()), round(end.y())))
    after = chart.objects[0]["anchors"]
    assert after != before
    assert after[1][0]-after[0][0] == pytest.approx(before[1][0]-before[0][0])
    assert after[1][1]-after[0][1] == pytest.approx(before[1][1]-before[0][1])


def test_magnet_search_respects_gaps_volume_and_visible_price_range(chart):
    rows = candles()
    missing = [int(rows[-50][0]), float(rows[-50][2])]
    del rows[-55:-44]
    chart.set_data(rows)
    chart.repaint()
    assert chart.drawing_anchor(chart.point(missing)) == pytest.approx(missing)
    assert chart.snap_target is None
    _, volume = chart.plot_rects()
    position = volume.center()
    assert chart.drawing_anchor(position) == pytest.approx(chart.anchor(position))
    assert chart.snap_target is None
    target = [chart.times[-20], chart.values[-20][1]]
    chart.auto_scale = False
    chart.high = target[1]-.03
    main, _ = chart.plot_rects()
    position = QPointF(chart.x(target[0]), main.top()+1)
    chart.drawing_anchor(position)
    assert chart.snap_target is None


def test_magnet_precision_survives_zoom_resize_and_history_prepend(chart):
    target = [chart.times[-40], chart.values[-40][1]]
    chart.auto_scale = False
    chart.follow = False
    for factor, width in ((.8, 1050), (1.2, 820)):
        chart.resize(width, 560)
        chart.zoom_time(factor, chart.x(target[0]))
        assert chart.drawing_anchor(chart.point(target)+QPointF(2, 4)) == target
    older = candles(300, begin=1700000000000-300*900000)
    chart.set_data(older+chart.rows)
    assert chart.drawing_anchor(chart.point(target)+QPointF(2, 4)) == target


def test_magnet_shared_between_pages_and_persisted(service, app):
    service.fetch_candles = lambda *_: None
    window = Workbench(service, lambda: None)
    try:
        first, second = window.chart, window.chart
        assert first.magnet_button.isChecked() and second.canvas.magnet_enabled
        first.magnet_button.click()
        assert not first.canvas.magnet_enabled and not second.magnet_action.isChecked()
        assert not ChartBook(service.store).magnet_enabled
        second.magnet_action.trigger()
        assert first.magnet_button.isChecked() and second.magnet_button.isChecked()
        assert ChartBook(service.store).magnet_enabled
        service.select(service.selected, "1H")
        assert first.canvas.magnet_enabled and second.canvas.magnet_enabled
    finally:
        window.deleteLater()
        app.processEvents()


def test_magnet_preview_tracks_changed_wick_but_keeps_fixed_first_anchor(chart):
    first = [chart.times[-80], chart.values[-80][2]]
    target = [chart.times[-20], chart.values[-20][1]]
    chart.set_tool("fib")
    chart.draw_click(chart.point(first))
    move_pointer(chart, chart.point(target)+QPointF(2, 4))
    rows = deepcopy(chart.rows)
    rows[-20][2] = str(target[1]+.01)
    chart.set_data(rows)
    expected = [target[0], float(rows[-20][2])]
    assert chart.preview["anchors"] == [first, expected]
    assert chart.snap_target == (*expected, "最高")
    assert not chart.book.objects(chart.environment, chart.instrument)


def test_ema_and_fib_dialog_save_custom_properties(chart):
    ema = EmaDialog(chart.book)
    ema.table.cellWidget(0, 1).setValue(7)
    ema.table.cellWidget(0, 2).set_color("#ff0000")
    ema.accept()
    assert chart.emas[0]["period"] == 7 and chart.emas[0]["color"] == "#ff0000"
    obj = drawing("fib", [[1700000000000, 101], [1700009000000, 110]])
    dialog = DrawingDialog(obj)
    dialog.levels.item(1, 0).setText("0.25")
    dialog.levels.item(1, 1).setText("四分之一")
    dialog.levels.cellWidget(1, 2).set_color("#ffffff")
    dialog.bars["1m"].setChecked(False)
    dialog.accept()
    assert dialog.result_object["levels"][1] == {"value": .25, "label": "四分之一", "color": "#ffffff"}
    assert "1m" not in dialog.result_object["bars"]
    bad = DrawingDialog(obj)
    bad.levels.item(0, 0).setText("nan")
    bad.accept()
    assert bad.result() == 0 and bad.error.text()


class FakeApi:
    def __init__(self):
        self.calls = []

    def get(self, path, callback, params=None, **kwargs):
        self.calls.append((path, callback, params))


def test_rest_and_stream_merge_without_downgrading_or_losing_history(service):
    service.api = FakeApi()
    pair = (service.selected, service.bar)
    rows = candles()
    rows[-1][8] = "0"
    service.candles[pair] = rows
    service.chart_feed.fetch(*pair)
    newer = deepcopy(rows[-1])
    newer[4], newer[2] = "120", "121"
    service.chart_feed.receive(pair, [newer])
    service.api.calls[-1][1](rows[-100:], None)
    assert len(service.candles[pair]) == 300
    assert service.candles[pair][-1][4] == "120"
    final = deepcopy(newer)
    final[8] = "1"
    service.chart_feed.receive(pair, [final])
    service.chart_feed.receive(pair, [rows[-1]])
    assert service.candles[pair][-1][4] == "120" and service.candles[pair][-1][8] == "1"


def test_history_pagination_retry_boundary_and_old_environment_callbacks(service):
    api = service.api = FakeApi()
    feed, pair = service.chart_feed, (service.selected, service.bar)
    service.candles[pair] = candles()
    feed.fetch(*pair, older=True)
    assert api.calls[-1][0].endswith("history-candles")
    assert api.calls[-1][2]["after"] == candles()[0][0]
    count = len(api.calls)
    feed.fetch(*pair, older=True)
    assert len(api.calls) == count
    api.calls[-1][1](None, ApiError("offline"))
    assert "重试" in feed.history_state[pair]
    feed.fetch(*pair, older=True)
    api.calls[-1][1](candles(300, begin=1700000000000-300*900000), None)
    assert len(service.candles[pair]) == 600
    feed.fetch(*pair, older=True)
    api.calls[-1][1]([], None)
    assert feed.history_state[pair] == "已到历史边界"
    feed.fetch(*pair)
    callback = api.calls[-1][1]
    feed.reset()
    snapshot = deepcopy(service.candles[pair])
    callback(candles(800), None)
    assert service.candles[pair] == snapshot


def test_outage_gap_is_repaired_before_freshness(service):
    api = service.api = FakeApi()
    feed, pair = service.chart_feed, (service.selected, service.bar)
    start = int(time.time()*1000)//900000*900000-900*900000
    service.candles[pair] = candles(100, start)
    feed.fetch(*pair)
    api.calls[-1][1](candles(301, start+600*900000), None)
    assert pair in feed.repairs and pair not in service.candle_times
    feed.repair(pair)
    api.calls[-1][1](candles(300, start+300*900000), None)
    feed.repair(pair)
    api.calls[-1][1](candles(300, start), None)
    assert pair not in feed.repairs
    # 完成补齐后重新校准最新数据，再恢复新鲜状态。
    api.calls[-1][1](candles(301, start+600*900000), None)
    assert pair in service.candle_times
    assert len(service.candles[pair]) == 901


def test_old_view_requests_target_range_directly(service):
    api = service.api = FakeApi()
    pair = (service.selected, service.bar)
    service.candles[pair] = candles()
    left = 1700000000000-900000*50000
    service.chart_feed.ensure_range(pair, left, 100)
    assert int(api.calls[-1][2]["after"]) <= left+101*900000
    api.calls[-1][1](candles(100, left), None)
    assert len(service.candles[pair]) == 400


@pytest.mark.parametrize("bad", ["nan", "inf", "-1", "0"])
def test_bad_candle_prices_rejected(bad):
    rows = candles(1)
    rows[0][1] = bad
    with pytest.raises(ValueError):
        valid_candles(rows)


class FakeSocket(QObject):
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    errorOccurred = pyqtSignal(object)
    textMessageReceived = pyqtSignal(str)
    def __init__(self):
        super().__init__()
        self.sent, self.aborted = [], False
    def setProxy(self, proxy):
        self.proxy = proxy
    def open(self, url):
        self.url = url
    def sendTextMessage(self, message):
        self.sent.append(message)
    def abort(self):
        self.aborted = True


def test_stream_subscription_heartbeats_switch_and_reconnect(app):
    sockets, received, states = [], [], []
    def factory():
        socket = FakeSocket()
        sockets.append(socket)
        return socket
    stream = CandleStream(QNetworkProxy(), lambda pair, rows: received.append((pair, rows)), states.append, socket_factory=factory)
    pair = ("BTC-USDT-SWAP", "15m")
    stream.select(pair)
    old = sockets[-1]
    old.connected.emit()
    assert json.loads(old.sent[-1])["args"] == [{"channel": "candle15m", "instId": pair[0]}]
    message = json.dumps({"arg": {"channel": "candle15m", "instId": pair[0]}, "data": candles(1)})
    old.textMessageReceived.emit(message)
    assert received[-1][0] == pair and states[-1] == "实时"
    stream.select(("ETH-USDT-SWAP", "1H"))
    old.textMessageReceived.emit(message)
    assert len(received) == 1 and old.aborted
    stream.last_message = time.monotonic()-35
    sockets[-1].textMessageReceived.emit("pong")
    stream.check()
    assert stream.retry.isActive() and "轮询" in states[-1]
    stream.close()
    assert not stream.retry.isActive() and stream.socket is None


def test_trade_lines_are_account_scoped_read_only_and_labeled(service):
    inst = service.selected
    service.positions = [{"instId": inst, "pos": "2", "posSide": "net", "avgPx": "100"},
                         {"instId": "OTHER-USDT-SWAP", "pos": "1", "avgPx": "5"}]
    service.pending_orders = [{"instId": inst, "side": "sell", "sz": "1", "px": "106"}]
    service.algos = [{"instId": inst, "side": "sell", "sz": "2", "slTriggerPx": "98", "tpTriggerPx": "110", "slTriggerPxType": "mark"}]
    lines = trade_lines(service)
    assert [r["price"] for r in lines] == [100, 106, 98, 110]
    assert "标记价" in lines[2]["label"] and "止损" in lines[2]["label"]
    assert service.store.list("local_order", service.scope) == []


def test_workbench_shared_chart_maximize_and_close_keeps_service(app, service):
    service.candles[(service.selected, service.bar)] = candles()
    service.fetch_candles = lambda *_: None
    window = Workbench(service, lambda: None)
    window.show()
    app.processEvents()
    window.chart.maximize()
    assert window.workspace.docks["ai"].isHidden() and window.workspace.docks["info"].isHidden()
    window.chart.maximize()
    assert not window.workspace.docks["ai"].isHidden() and not window.workspace.docks["info"].isHidden()
    menus = window.chart.findChildren(QMenu)
    menus[0].actions()[1].trigger()
    assert window.chart.canvas.tool == "trend"
    window.chart.canvas.draw_click(QPointF(240, 180))
    window.chart.canvas.draw_click(QPointF(400, 220))
    assert window.chart.canvas.tool == 'trend'
    assert window.chart.tool_buttons['trend'].isChecked()
    QTest.keyClick(window.chart.canvas, Qt.Key.Key_Escape)
    assert window.chart.canvas.tool == 'cursor'
    assert window.chart.tool_buttons['cursor'].isChecked()
    window.chart.choose_tool("cursor")
    window.chart.canvas.zoom_time(.8, 400)
    window.pages.setCurrentIndex(1)
    app.processEvents()
    count = window.chart.canvas.count
    window.pages.setCurrentIndex(0)
    app.processEvents()
    assert window.chart.canvas.count == count
    window.chart.maximize()
    assert not window.trade.sidebar.isVisible()
    window.chart.maximize()
    window.close()
    assert not service.closed
    window.deleteLater()
    app.processEvents()


def test_old_stream_frame_does_not_refresh_cached_recent_candle(service):
    pair = (service.selected, service.bar)
    start = int(time.time()*1000)-900000*299
    rows = candles(300, start)
    service.candles[pair] = rows
    service.chart_feed.receive(pair, rows[:1])
    assert pair not in service.candle_times


def test_volume_rule_requires_continuous_closed_candles():
    from coinpilot_ai.trading.alerts import AlertEngine
    engine = AlertEngine()
    pair = ("BTC-USDT-SWAP", "15m")
    rows = candles(30)
    condition = {"metric": "volume_ratio", "bar": "15m"}
    assert engine.metric(condition, pair[0], {}, [], {pair: rows}, 0, True) is not None
    del rows[-10]
    assert engine.metric(condition, pair[0], {}, [], {pair: rows}, 0, True) is None


def test_visible_render_performance_5000_candles_100_objects(chart):
    chart.set_data(candles(5000))
    chart.objects = [drawing("trend", [[chart.times[-95+i%50], 150+i*.01], [chart.times[-20+i%15], 151+i*.01]]) for i in range(100)]
    chart.repaint()
    samples = []
    for i in range(25):
        start = time.perf_counter()
        chart.pointer = QPointF(300+i*2, 200)
        chart.repaint()
        samples.append((time.perf_counter()-start)*1000)
    assert chart.visible()[1]-chart.visible()[0] < 110
    p95 = sorted(samples)[int(len(samples)*.95)]
    print(f"chart paint p95={p95:.2f}ms, median={statistics.median(samples):.2f}ms")
    assert p95 < 50


def finish_series(app, series):
    deadline = time.monotonic()+5
    while series.job is not None and time.monotonic() < deadline:
        app.processEvents()
    assert series.job is None


def assert_series_matches_full(series, rows):
    assert series.data.rows == rows
    assert series.data.times == [int(r[0]) for r in rows]
    assert series.data.values == [[float(v) for v in r[1:6]] for r in rows]
    for period, result in zip(series.data.periods, series.data.emas):
        assert result == pytest.approx(ema_values([float(r[4]) for r in rows], period), rel=1e-13)


@pytest.mark.parametrize("count", [5000, 50000])
def test_shared_tail_updates_do_constant_work(service, app, count):
    feed, pair = service.chart_feed, (service.selected, service.bar)
    rows = candles(count)
    rows[-1][8] = "0"
    service.candles[pair] = rows
    series = feed.get_series(pair)
    finish_series(app, series)
    index = feed.raw_index(pair)
    changes = []
    feed.series_changed.connect(changes.append)
    before = series.converted_rows
    for n in range(20):
        row = list(rows[-1])
        row[4] = str(float(row[1])+.01*n)
        feed.receive(pair, [row])
    assert series.converted_rows-before == 20
    assert len(changes) == 20
    assert feed.raw_index(pair) is index
    assert feed.get_series(pair) is series
    assert_series_matches_full(series, rows)
    revision = series.revision
    feed.receive(pair, [rows[-1]])
    assert series.revision == revision and series.converted_rows-before == 20
    appended = candles(1, int(rows[-1][0])+900000)
    feed.merge(pair, appended)
    assert series.converted_rows-before == 21
    assert changes[-1].first == int(appended[0][0])
    assert_series_matches_full(series, rows)


def test_history_job_publishes_complete_result_with_interleaved_ticks(service, app):
    feed, pair = service.chart_feed, (service.selected, service.bar)
    service.candles[pair] = candles(5000)
    series = feed.get_series(pair)
    finish_series(app, series)
    previous = series.data
    changes = []
    feed.series_changed.connect(changes.append)
    feed.merge(pair, candles(300, 1700000000000-300*900000))
    assert series.job is not None and series.data is previous
    series._step()
    assert series.data is previous
    rows = service.candles[pair]
    changed = list(rows[20])
    changed[4] = str(float(changed[4])+.2)
    feed.merge(pair, [changed])
    last = list(rows[-1])
    last[4] = str(float(last[4])+.3)
    feed.merge(pair, [last])
    feed.merge(pair, candles(2, int(rows[-1][0])+900000))
    assert previous.rows[-1] != last and len(previous.rows) == 5000
    finish_series(app, series)
    assert len(changes) == 1 and changes[0].structural
    assert_series_matches_full(series, rows)


def test_historical_correction_reuses_prefix_and_period_change_cancels_old_job(service, app):
    feed, pair = service.chart_feed, (service.selected, service.bar)
    service.candles[pair] = candles(5000)
    series = feed.get_series(pair)
    finish_series(app, series)
    before = series.converted_rows
    row = list(service.candles[pair][-500])
    row[4] = str(float(row[4])+.4)
    feed.merge(pair, [row])
    finish_series(app, series)
    assert series.converted_rows-before == 500
    assert_series_matches_full(series, service.candles[pair])
    feed.merge(pair, candles(300, 1700000000000-300*900000))
    assert series.job is not None
    settings = [dict(service.chart_book.emas[0], period=p) for p in (1, 7, 1000)]
    service.chart_book.save_emas(settings)
    finish_series(app, series)
    assert series.data.periods == (1, 7, 1000)
    assert_series_matches_full(series, service.candles[pair])


def test_feed_reset_cancels_pending_series_publication(service, app):
    feed, pair = service.chart_feed, (service.selected, service.bar)
    service.candles[pair] = candles(50000)
    series = feed.get_series(pair)
    changes = []
    feed.series_changed.connect(changes.append)
    assert series.job is not None
    feed.reset()
    assert series.job is None and not series.timer.isActive()
    app.processEvents()
    assert changes == [] and feed.series == {} and feed.raw_indexes == {}


def test_cached_hover_and_old_view_ignore_unrelated_tail_updates(chart, service, app):
    feed, pair = service.chart_feed, (service.selected, service.bar)
    service.candles[pair] = candles(5000)
    series = feed.get_series(pair)
    finish_series(app, series)
    chart.bind_series(series)
    feed.series_changed.connect(lambda change: chart.bind_series(series, change))
    chart.follow = False
    chart.left_time = series.data.times[200]
    chart.objects = [drawing("trend", [[chart.times[210], 102], [chart.times[260], 104]])]
    chart.repaint()
    baseline = chart.market_builds, chart.object_builds
    for i in range(10):
        chart.pointer = QPointF(200+i*10, 180)
        chart.repaint()
    assert (chart.market_builds, chart.object_builds) == baseline
    last = list(service.candles[pair][-1])
    last[4] = str(float(last[4])+.2)
    feed.merge(pair, [last])
    chart.repaint()
    assert (chart.market_builds, chart.object_builds) == baseline
    # 视口之前的修正会影响可见 EMA，即使可见蜡烛价格没有变化也必须重建。
    first = list(service.candles[pair][0])
    first[4] = "100.5"
    feed.merge(pair, [first])
    finish_series(app, series)
    chart.repaint()
    assert chart.market_builds == baseline[0]+1
    assert chart.object_builds == baseline[1]


def test_object_drag_reuses_fixed_layer_and_invalidates_theme_size(chart):
    chart.objects = [drawing("trend", [[chart.times[-80], 102], [chart.times[-30], 104]])]
    chart.selected_id = chart.objects[0]["id"]
    chart.drag = {"mode": "object", "handle": None}
    chart.repaint()
    baseline = chart.market_builds, chart.object_builds
    for i in range(5):
        chart.objects[0]["anchors"][0][1] += .01
        chart.repaint()
    assert (chart.market_builds, chart.object_builds) == baseline
    chart.drag = None
    chart.repaint()
    assert chart.object_builds == baseline[1]+1
    chart.apply_theme("okx_dark")
    chart.repaint()
    assert chart.market_builds == baseline[0]+1
    chart.resize(1100, 650)
    chart.repaint()
    assert chart.market_builds == baseline[0]+2
    chart.devicePixelRatioF = lambda: 2.
    chart.repaint()
    assert chart._market_layer.devicePixelRatioF() == 2
    assert chart.market_builds == baseline[0]+3


def test_hidden_panels_share_calculation_and_resume_latest_snapshot(service, app):
    service.fetch_candles = lambda *_: None
    feed, pair = service.chart_feed, (service.selected, service.bar)
    service.candles[pair] = candles(5000)
    series = feed.get_series(pair)
    finish_series(app, series)
    window = Workbench(service, lambda: None)
    try:
        window.show()
        app.processEvents()
        panel = window.chart
        assert panel.canvas._series is series
        window.workspace.set_visible("market", False)
        baseline = panel.canvas.market_builds
        before = series.converted_rows
        for i in range(10):
            row = list(service.candles[pair][-1])
            row[4] = str(float(row[4])+.01)
            feed.merge(pair, [row])
        assert series.converted_rows-before == 10
        assert not panel.refresh_timer.isActive() and not panel.range_timer.isActive()
        assert panel.canvas.market_builds == baseline
        calls = []
        feed.ensure_range = lambda *args: calls.append(args)
        service.running = True
        panel.canvas.follow = False
        panel.ensure_view()
        assert calls == []
        window.workspace.set_visible("market", True)
        app.processEvents()
        assert panel.canvas._series is series
        assert panel.canvas._series_revision == series.revision
        baseline = panel.canvas.market_builds
        panel.refresh("rules")
        assert not panel.refresh_timer.isActive()
        panel.canvas.repaint()
        assert panel.canvas.market_builds == baseline
    finally:
        service.running = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_series_publication_can_receive_reentrant_tail_update(app):
    rows = candles(1000)
    series = ChartSeries(("BTC-USDT-SWAP", "15m"), [20, 60])
    calls = []

    def received(change):
        calls.append(change)
        if len(calls) == 1:
            rows[-1] = list(rows[-1])
            rows[-1][4] = str(float(rows[-1][4])+.1)
            series.update(rows, len(rows)-1)

    series.changed.connect(received)
    series.update(rows, 0, structural=True)
    finish_series(app, series)
    assert len(calls) == 2 and series.pending is None
    assert_series_matches_full(series, rows)
    series.deleteLater()


def test_restored_auto_view_recalculates_price_cache(chart):
    chart.fit_prices()
    expected = chart.low, chart.high
    chart.restore_view({"left_time": chart.left_time, "count": chart.count,
                        "auto": True, "low": 0, "high": 1})
    chart.repaint()
    assert (chart.low, chart.high) == expected


def test_coalesced_updates_preserve_earliest_affected_time(service, app):
    service.fetch_candles = lambda *_: None
    feed, pair = service.chart_feed, (service.selected, service.bar)
    service.candles[pair] = candles(300)
    series = feed.get_series(pair)
    window = Workbench(service, lambda: None)
    try:
        window.show()
        app.processEvents()
        panel = window.chart
        panel.flush_refresh()
        panel.canvas.follow = False
        panel.canvas.left_time = series.data.times[190]
        panel.canvas.count = 30
        panel.canvas.repaint()
        builds = panel.canvas.market_builds
        for index in (200, 299):
            row = list(service.candles[pair][index])
            row[4] = str(float(row[4])+.1)
            feed.merge(pair, [row])
        assert panel.pending_change.first == series.data.times[200]
        assert panel.refresh_timer.isActive()
        panel.flush_refresh()
        panel.canvas.repaint()
        assert panel.canvas.market_builds == builds+1
        assert panel.pending_change is None
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
