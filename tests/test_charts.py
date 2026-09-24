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

from coinpilot_ai.config import DEFAULT_CONFIG
from coinpilot_ai.cockpit.chart import CandleChart
from coinpilot_ai.cockpit.chart_dialogs import DrawingDialog, EmaDialog
from coinpilot_ai.cockpit.chart_feed import CandleStream, valid_candles
from coinpilot_ai.cockpit.chart_state import ChartBook, drawing, ema_values, trade_lines
from coinpilot_ai.cockpit.service import CockpitService
from coinpilot_ai.cockpit.store import Store
from coinpilot_ai.cockpit.transport import ApiError
from coinpilot_ai.cockpit.workbench import Workbench


class EmptyVault:
    def read(self, key):
        return None


def candles(count=300, begin=1700000000000, interval=900000):
    return [[str(begin+i*interval), str(100+i*.01), str(102+i*.01), str(98+i*.01), str(101+i*.01), str(10+i%17), "0", "0", "1"] for i in range(count)]


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
    from coinpilot_ai.cockpit import chart as module
    monkeypatch.setattr(module.QInputDialog, "getText", lambda *a: ("观察支撑", True))
    chart.set_tool(tool)
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(240, 180))
    if tool not in ("horizontal", "vertical", "text"):
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(500, 240))
    assert len(chart.objects) == 1
    chart.repaint()
    obj = chart.objects[0]
    before = deepcopy(obj["anchors"])
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


def test_ema_numerical_values_and_view_independence(chart):
    assert ema_values([1, 2, 3, 4], 3) == [1, 1.5, 2.25, 3.125]
    before = deepcopy(chart.ema_cache)
    chart.zoom_time(1.4, 300)
    chart.latest()
    chart.repaint()
    assert chart.ema_cache == before


def move_pointer(chart, position, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QMouseEvent(QMouseEvent.Type.MouseMove, position, position, Qt.MouseButton.NoButton,
                       Qt.MouseButton.NoButton, modifiers)
    chart.mouseMoveEvent(event)


@pytest.mark.parametrize("tool", ["trend", "fib", "ray", "rectangle", "measure", "horizontal", "vertical", "text"])
def test_drawing_magnet_snaps_exact_wick_time_and_high_low(chart, monkeypatch, tool):
    from coinpilot_ai.cockpit import chart as module
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
        first, second = window.chart, window.trade.chart
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
    assert window.monitor_sidebar.isHidden() and window.monitor_bottom.isHidden()
    window.chart.maximize()
    assert not window.monitor_sidebar.isHidden() and not window.monitor_bottom.isHidden()
    menus = window.chart.findChildren(QMenu)
    menus[0].actions()[1].trigger()
    assert window.chart.canvas.tool == "trend"
    window.chart.choose_tool("cursor")
    window.chart.canvas.zoom_time(.8, 400)
    window.pages.setCurrentIndex(1)
    app.processEvents()
    assert window.trade.chart.canvas.count == window.chart.canvas.count
    window.trade.chart.maximize()
    assert window.trade.sidebar.isHidden()
    window.trade.chart.maximize()
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
    from coinpilot_ai.cockpit.alerts import AlertEngine
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
