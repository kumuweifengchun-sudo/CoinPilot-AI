"""原生交互 K 线画布；视口使用时间坐标，历史补页不会移动屏幕内容。"""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QColorDialog, QInputDialog, QMenu, QToolTip, QWidget

from coinpilot_ai.market.intervals import BARS
from coinpilot_ai.charts.state import default_emas, drawing, ema_values, name_drawings
from .render import ChartRenderer
from .regions import enclosed_region
from coinpilot_ai.market.indicators import IndicatorResult, calculate
from coinpilot_ai.ui.theme import events


class CandleChart(ChartRenderer, QWidget):
    history_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    edit_requested = pyqtSignal(str)
    tool_finished = pyqtSignal()
    view_changed = pyqtSignal()
    magnet_changed = pyqtSignal(bool)
    crosshair_changed = pyqtSignal(object)
    price_alert_requested = pyqtSignal(str, str, str)
    order_requested = pyqtSignal(str, str, str, str)
    risk_requested = pyqtSignal(str, str, str)
    SNAP_RADIUS = 14.  # Qt 逻辑像素；所有缩放和 DPI 下具有相同的操作距离。

    def __init__(self, service=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.book = service.chart_book if service else None
        self.environment, self.instrument, self.bar = "demo", "BTC-USDT-SWAP", "15m"
        self.rows, self.values, self.times, self.ema_cache = [], [], [], []
        self.emas = deepcopy(self.book.emas if self.book else default_emas())
        self.indicator_results = []
        self.indicators_override = None
        self.view_key = None
        self.objects, self.hit_paths = [], {}
        self.selected_id = None
        self.tool = "cursor"
        self.preview = self.pointer = self.drag = None
        self.pending_draw = None
        self.draw_click_timer = QTimer(self)
        self.draw_click_timer.setSingleShot(True)
        self.draw_click_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.draw_click_timer.timeout.connect(self.finish_pending_draw)
        self.snap_target = None
        self.external_crosshair_time = None
        self.magnet_enabled = self.book.magnet_enabled if self.book else True
        self.overlays = []
        self.account_stale = False
        self.last_price = None
        self.price_stale = True
        self.title = "等待 OKX K 线"
        self.count, self.left_time = 100., None
        self.follow = self.auto_scale = True
        self.low, self.high = 0., 1.
        self.volume_ratio = .22
        self._saving = False
        self._history_latch = None
        self._series = None
        self._series_revision = -1
        self._plot_revision = self._theme_revision = 0
        self._market_key = self._objects_key = self._fit_key = None
        self._fixed_hit_paths = {}
        self.market_builds = self.object_builds = 0
        self.frame_timer = QTimer(self)
        self.frame_timer.setSingleShot(True)
        self.frame_timer.setInterval(16)
        self.frame_timer.timeout.connect(self.update)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(180)
        self.save_timer.timeout.connect(self.save_view)
        self.setMinimumSize(260, 180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        events.changed.connect(self.apply_theme)
        if self.book:
            self.book.changed.connect(self.book_changed)

    def apply_theme(self, _theme_id):
        self._theme_revision += 1
        self.update()

    def request_frame(self):
        if self.isVisible() and not self.frame_timer.isActive():
            self.frame_timer.start()

    def set_context(self, environment, instrument, bar):
        if (environment, instrument, bar) == (self.environment, self.instrument, self.bar) and self.title != "等待 OKX K 线":
            return
        self.flush_view()
        self.cancel_pending_draw()
        self.environment, self.instrument, self.bar = environment, instrument, bar
        self.title = instrument.replace("-USDT-SWAP", " / USDT") + " · " + bar + " · OKX"
        self.rows, self.values, self.times, self.ema_cache = [], [], [], []
        self._series = None
        self._series_revision = -1
        self._plot_revision += 1
        self.pointer = self.drag = self.preview = None
        self.snap_target = None
        self.selected_id = self._history_latch = None
        view = self.book.view(environment, instrument, bar) if self.book else {}
        if self.view_key and self.book:
            view = self.book.store.get("chart_pane_view", f"{self.view_key}/{environment}/{instrument}/{bar}", view)
        self.restore_view(view)
        self.objects = self.book.objects(environment, instrument) if self.book else []

    def restore_view(self, data):
        self._fit_key = None
        self.count = max(15., min(2000., float(data.get("count", 100))))
        self.left_time = data.get("left_time")
        self.follow, self.auto_scale = data.get("follow", True), data.get("auto", True)
        self.low, self.high = data.get("low", 0.), data.get("high", 1.)
        self.volume_ratio = max(.10, min(.45, data.get("volume_ratio", .22)))
        self.update()
        self.view_changed.emit()

    def book_changed(self, kind, key):
        if kind == "ema":
            if self._series is None:
                self.emas = deepcopy(self.book.emas)
                self.calculate_emas()
            elif tuple(e["period"] for e in self.book.emas) == self._series.data.periods:
                self.emas = deepcopy(self.book.emas)
        elif kind == "magnet":
            self.magnet_enabled = self.book.magnet_enabled
            self.snap_target = None
            self.magnet_changed.emit(self.magnet_enabled)
            self.refresh_drawing_pointer()
        elif kind == "indicators":
            self.calculate_indicators()
            self._plot_revision += 1
        elif kind == "drawings" and key == (self.environment, self.instrument):
            self.objects = self.book.objects(*key)
            if not any(o["id"] == self.selected_id for o in self.objects):
                self.selected_id = None
        elif kind == "view" and not self.view_key and not self._saving and key == (self.environment, self.instrument, self.bar):
            self.restore_view(self.book.view(*key))
        self.update()

    def save_view(self):
        if not self.book or not self.times or self.service.closed:
            return
        self._saving = True
        try:
            view = {"count": self.count, "left_time": self.left_time, "follow": self.follow,
                    "auto": self.auto_scale, "low": self.low, "high": self.high,
                    "volume_ratio": self.volume_ratio}
            if self.view_key:
                self.book.store.put("chart_pane_view",
                                    f"{self.view_key}/{self.environment}/{self.instrument}/{self.bar}", view)
            else:
                self.book.save_view(self.environment, self.instrument, self.bar, view)
        finally:
            self._saving = False

    def flush_view(self):
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.save_view()

    def persist_objects(self):
        if self.book and not self.service.closed:
            self.book.save_objects(self.environment, self.instrument, self.objects)
        elif not self.book:
            name_drawings(self.objects)
        self.update()

    def set_data(self, rows, title=None):
        if title:
            self.title = title
        if rows is self.rows:
            return
        try:
            values = [[float(v) for v in row[1:6]] for row in rows]
            times = [int(row[0]) for row in rows]
        except (ValueError, TypeError, IndexError):
            return
        self.rows, self.values, self.times = rows, values, times
        self._series = None
        self._plot_revision += 1
        if times and (self.follow or self.left_time is None):
            self.left_time = times[-1] - (self.count-5)*self.interval
        self.calculate_emas()
        self.calculate_indicators()
        self.refresh_drawing_pointer()
        self.update()

    def calculate_emas(self):
        closes = [v[3] for v in self.values]
        self.ema_cache = [ema_values(closes, e["period"]) for e in self.emas]
        self._plot_revision += 1

    def bind_series(self, series, change=None):
        """生产图表使用共享数值；版本而非原始列表身份决定是否刷新。"""
        if self._series is series and self._series_revision == series.revision:
            return
        different = self._series is not series
        data = series.data
        if different or change is None or change.structural or self.left_time is None or (
                change.first is not None and change.first <= self.left_time+(self.count+1)*self.interval
                and change.last >= self.left_time-self.interval):
            self._plot_revision += 1
        self._series, self._series_revision = series, series.revision
        self.rows, self.values, self.times, self.ema_cache = data.rows, data.values, data.times, data.emas
        self.calculate_indicators()
        if self.book and tuple(e["period"] for e in self.book.emas) == data.periods:
            self.emas = deepcopy(self.book.emas)
        if self.times and (self.follow or self.left_time is None):
            self.left_time = self.times[-1]-(self.count-5)*self.interval
        self.refresh_drawing_pointer()
        self.update()

    def calculate_indicators(self):
        self.indicator_results = []
        if self.book and self.rows:
            for setting in (self.indicators_override if self.indicators_override is not None else self.book.indicators):
                if setting["visible"]:
                    try:
                        source_bar = setting.get("bar", "chart")
                        if source_bar not in ("chart", self.bar) and self.service:
                            source = self.service.chart_feed.get_series((self.instrument, source_bar))
                            result = self.service.chart_feed.indicator(source, setting)
                            result = self.align_indicator(result, source_bar)
                        else:
                            result = (self.service.chart_feed.indicator(self._series, setting)
                                      if self.service and self._series is not None
                                      and self._series_revision == self._series.revision
                                      else calculate(self.rows, setting, bar=self.bar))
                        self.indicator_results.append((setting, result))
                    except (ValueError, TypeError, IndexError):
                        continue

    def align_indicator(self, result, source_bar):
        """跨周期只使用在目标 K 线时点已经确认的源 K 线。"""
        source_close = [stamp+BARS[source_bar]*1000 for stamp in result.times]
        chart_times = tuple(int(row[0]) for row in self.rows)
        lines = {name: [] for name in result.lines}
        confirmed = []
        index = -1
        for stamp, row in zip(chart_times, self.rows):
            cutoff = stamp+BARS[self.bar]*1000
            while index+1 < len(source_close) and source_close[index+1] <= cutoff:
                index += 1
            usable = index
            while usable >= 0 and not result.confirmed[usable]:
                usable -= 1
            for name, values in result.lines.items():
                lines[name].append(values[usable] if usable >= 0 else None)
            confirmed.append(str(row[8]) == "1" and usable >= 0)
        return IndicatorResult(result.kind, chart_times,
                               {name: tuple(values) for name, values in lines.items()}, tuple(confirmed))
        self.update()

    @property
    def interval(self):
        return BARS[self.bar] * 1000

    def index_at(self, stamp):
        if not self.times:
            return 0.
        return (stamp-self.times[0])/self.interval

    def time_at(self, index):
        if not self.times:
            return 0.
        return self.times[0] + index*self.interval

    def plot_rects(self):
        width = max(80, self.width()-94)
        available = max(75, self.height()-87)
        volume_height = available * self.volume_ratio
        main = QRectF(8, 55, width, available-volume_height-8)
        volume = QRectF(8, main.bottom()+8, width, volume_height)
        return main, volume

    def left_index(self):
        return self.index_at(self.left_time if self.left_time is not None else (self.times[0] if self.times else 0))

    def visible(self):
        if not self.times:
            return 0, 0
        left = self.left_time if self.left_time is not None else self.times[0]
        return max(0, bisect_left(self.times, left)-1), min(len(self.times), bisect_right(self.times, left+self.count*self.interval)+1)

    def fit_prices(self):
        if not self.auto_scale or not self.values:
            self._fit_key = None
            return
        key = (self._plot_revision, self.left_time, self.count)
        if key == self._fit_key:
            return
        self._fit_key = key
        start, end = self.visible()
        rows = self.values[start:end]
        if rows:
            low, high = min(v[2] for v in rows), max(v[1] for v in rows)
            pad = max((high-low)*.08, abs(high)*.0001, 1e-10)
            self.low, self.high = low-pad, high+pad

    def x(self, stamp):
        main, _ = self.plot_rects()
        return main.left() + (self.index_at(stamp)-self.left_index()+.5)*main.width()/self.count

    def y(self, price):
        main, _ = self.plot_rects()
        return main.bottom() - (price-self.low)/max(1e-14, self.high-self.low)*main.height()

    def anchor(self, position):
        main, _ = self.plot_rects()
        index = self.left_index() + (position.x()-main.left())/main.width()*self.count-.5
        price = self.low + (main.bottom()-position.y())/main.height()*(self.high-self.low)
        return [self.time_at(index), price]

    def point(self, anchor):
        return QPointF(self.x(anchor[0]), self.y(anchor[1]))

    def set_magnet(self, enabled):
        if self.book:
            if not self.service.closed:
                self.book.save_magnet(enabled)
        else:
            self.magnet_enabled = bool(enabled)
            self.snap_target = None
            self.magnet_changed.emit(self.magnet_enabled)
            self.refresh_drawing_pointer()
        self.update()

    def drawing_anchor(self, position, modifiers=Qt.KeyboardModifier.NoModifier):
        """只为绘图锚点吸附高低价；视口平移和整对象平移仍使用普通反向坐标。"""
        self.snap_target = None
        self.fit_prices()
        raw = self.anchor(position)
        main, _ = self.plot_rects()
        if not self.magnet_enabled or not self.times or not main.contains(position) or modifiers & Qt.KeyboardModifier.AltModifier:
            return raw
        radius = self.SNAP_RADIUS
        span = radius / main.width() * self.count * self.interval
        start = bisect_left(self.times, raw[0]-span)
        end = bisect_right(self.times, raw[0]+span)
        nearest = radius*radius
        for i in range(start, end):
            for field, kind in ((1, "最高"), (2, "最低")):
                target = [self.times[i], self.values[i][field]]
                point = self.point(target)
                if not main.contains(point):
                    continue
                distance = (point.x()-position.x())**2 + (point.y()-position.y())**2
                if distance <= nearest:
                    nearest = distance
                    self.snap_target = (*target, kind)
        return list(self.snap_target[:2]) if self.snap_target else raw

    def refresh_drawing_pointer(self, modifiers=None):
        if self.pointer is None:
            return
        if modifiers is None:
            from PyQt6.QtWidgets import QApplication
            modifiers = QApplication.keyboardModifiers()
        if self.tool != "cursor" and not self.drag:
            anchor = self.drawing_anchor(self.pointer, modifiers)
            if self.preview:
                self.preview["anchors"][-1] = anchor
        elif self.drag and self.drag["mode"] == "object" and self.drag["handle"] is not None:
            obj = self.selected()
            if obj:
                handle = self.drag["handle"]
                position = self.point(self.drag["before"]["anchors"][handle])+self.pointer-self.drag["pos"]
                obj["anchors"][handle] = self.drawing_anchor(position, modifiers)
        self.request_frame()

    def change_view(self):
        self.snap_target = None
        self.fit_prices()
        self.save_timer.start()
        self.view_changed.emit()
        self.update()
        if self.times and self.left_index() < 40 and self._history_latch != self.times[0]:
            self._history_latch = self.times[0]
            self.history_requested.emit()

    def latest(self):
        self.follow = True
        if self.times:
            self.left_time = self.times[-1] - (self.count-5)*self.interval
        self.change_view()

    def auto(self):
        self.auto_scale = True
        self._fit_key = None
        self.change_view()

    def zoom_time(self, factor, pixel):
        main, _ = self.plot_rects()
        fraction = max(0, min(1, (pixel-main.left())/main.width()))
        fixed_index = self.left_index()+fraction*self.count
        self.count = max(15., min(2000., self.count*factor))
        self.left_time = self.time_at(fixed_index-fraction*self.count)
        self.follow = False
        self.change_view()

    def wheelEvent(self, event):
        if self.times:
            self.zoom_time(math.exp(-event.angleDelta().y()/120*.16), event.position().x())
        event.accept()

    def set_tool(self, tool):
        self.cancel_pending_draw()
        self.tool, self.preview, self.drag = tool, None, None
        self.snap_target = None
        self.setCursor(Qt.CursorShape.ArrowCursor if tool == "cursor" else Qt.CursorShape.CrossCursor)
        self.setFocus()
        self.update()

    def selected(self):
        return next((o for o in self.objects if o["id"] == self.selected_id), None)

    def delete_selected(self):
        self.delete_objects({self.selected_id})

    def delete_objects(self, identities):
        self.cancel_pending_draw()
        removed = {o['id'] for o in self.objects if o['id'] in identities and not o['locked']}
        if removed:
            self.objects = [o for o in self.objects if o['id'] not in removed]
            if self.selected_id in removed:
                self.selected_id = None
            self.persist_objects()
        return len(removed)

    def hit_test(self, pos):
        self.fit_prices()
        self.object_layers()
        obj = self.selected()
        if obj and not obj.get("hidden") and self.bar in obj.get("bars", BARS):
            for i, anchor in enumerate(obj["anchors"]):
                p = self.point(anchor)
                if abs(p.x()-pos.x()) < 9 and abs(p.y()-pos.y()) < 9:
                    return obj["id"], i
        for identity, path in reversed(list(self.hit_paths.items())):
            if path.contains(pos):
                return identity, None
        return None, None

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() != Qt.MouseButton.LeftButton or not self.times:
            return
        self.finish_pending_draw()
        pos = event.position()
        main, volume = self.plot_rects()
        self.fit_prices()
        if pos.y() < 52:
            if pos.y() >= 24:
                self.settings_requested.emit()
            return
        if main.bottom() <= pos.y() <= volume.top():
            self.drag = {"mode": "volume", "pos": pos, "ratio": self.volume_ratio}
        elif pos.x() > main.right() and 55 <= pos.y() <= main.bottom():
            self.drag = {"mode": "price", "pos": pos, "low": self.low, "high": self.high}
            self.auto_scale = False
        elif pos.y() >= volume.bottom():
            self.drag = {"mode": "time", "pos": pos, "count": self.count, "left": self.left_index()}
        elif main.contains(pos) or volume.contains(pos):
            if self.tool != "cursor" and main.contains(pos):
                if self.hit_test(pos)[0]:
                    # 线上点击先等待双击判定，避免编辑属性时误新增或完成画线。
                    self.pointer = pos
                    self.pending_draw = self.drawing_anchor(pos, event.modifiers())
                    self.draw_click_timer.start(QApplication.doubleClickInterval())
                else:
                    self.draw_click(pos, event.modifiers())
            else:
                identity, handle = self.hit_test(pos)
                self.selected_id = identity
                obj = self.selected()
                if obj:
                    if not obj["locked"]:
                        self.drag = {"mode": "object", "pos": pos, "handle": handle, "before": deepcopy(obj)}
                else:
                    self.drag = {"mode": "pan", "pos": pos, "left": self.left_index(), "low": self.low, "high": self.high}
        self.update()

    def cancel_pending_draw(self):
        self.draw_click_timer.stop()
        self.pending_draw = None

    def finish_pending_draw(self):
        anchor = self.pending_draw
        self.cancel_pending_draw()
        if anchor is not None:
            self.draw_anchor(anchor)
            self.update()

    def draw_click(self, pos, modifiers=Qt.KeyboardModifier.NoModifier):
        self.pointer = pos
        self.draw_anchor(self.drawing_anchor(pos, modifiers))

    def draw_anchor(self, a):
        if self.preview is not None:
            self.preview["anchors"][-1] = a
            obj = self.preview
        elif self.tool in ("horizontal", "vertical", "text"):
            text = ""
            if self.tool == "text":
                text, ok = QInputDialog.getText(self, "图表文字", "文字内容")
                if not ok or not text.strip():
                    return
            obj = drawing(self.tool, [a], text)
        else:
            self.preview = drawing(self.tool, [a, a])
            return
        self.objects.append(obj)
        self.selected_id = obj["id"]
        self.persist_objects()
        # 完成当前对象后清空预览，保留工具，下一次点击开始新的对象。
        self.set_tool(self.tool)

    def mouseMoveEvent(self, event):
        pos = event.position()
        self.pointer = pos
        main, _ = self.plot_rects()
        if self.times and main.left() <= pos.x() <= main.right():
            stamp = self.left_time + (pos.x()-main.left())/main.width()*self.count*self.interval
            self.crosshair_changed.emit(stamp)
        else:
            self.crosshair_changed.emit(None)
        self.snap_target = None
        if self.tool != "cursor" and not self.drag:
            self.refresh_drawing_pointer(event.modifiers())
        if self.drag:
            d = self.drag
            dx, dy = pos.x()-d["pos"].x(), pos.y()-d["pos"].y()
            if d["mode"] == "pan":
                self.left_time = self.time_at(d["left"]-dx/main.width()*self.count)
                self.follow = False
                if abs(dy) > 3 or not self.auto_scale:
                    self.auto_scale = False
                    delta = dy/main.height()*(d["high"]-d["low"])
                    self.low, self.high = d["low"]+delta, d["high"]+delta
            elif d["mode"] == "price":
                center = (d["low"]+d["high"])/2
                span = max(1e-12, (d["high"]-d["low"])*math.exp(max(-10, min(10, dy/150))))
                self.low, self.high = center-span/2, center+span/2
            elif d["mode"] == "time":
                self.count = max(15., min(2000., d["count"]*math.exp(max(-6, min(6, -dx/250)))))
                self.left_time = self.time_at(d["left"]+d["count"]-self.count)
                self.follow = False
            elif d["mode"] == "volume":
                self.volume_ratio = max(.1, min(.45, d["ratio"]-dy/max(75, self.height()-87)))
            elif d["mode"] == "object":
                obj = self.selected()
                if obj:
                    for i, anchor in enumerate(d["before"]["anchors"]):
                        if d["handle"] is None or d["handle"] == i:
                            position = self.point(anchor)+QPointF(dx, dy)
                            obj["anchors"][i] = (self.anchor(position) if d["handle"] is None
                                                 else self.drawing_anchor(position, event.modifiers()))
            if d["mode"] != "object":
                self.fit_prices()
                self.view_changed.emit()
        self.request_frame()

    def mouseReleaseEvent(self, event):
        if self.drag and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            mode = self.drag["mode"]
            self.drag = None
            self.snap_target = None
            self.persist_objects() if mode == "object" else self.change_view()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.times:
            return
        main, volume = self.plot_rects()
        if main.contains(event.position()):
            identity, _ = self.hit_test(event.position())
            if identity:
                self.set_tool(self.tool)
                self.selected_id = identity
                self.edit_requested.emit(identity)
                event.accept()
                return
        if self.tool != 'cursor':
            return
        self.drag = None
        if event.position().x() > main.right():
            self.auto()
        elif event.position().y() > volume.bottom():
            self.count = 100
            self.latest()
        elif main.contains(event.position()):
            self.copy_price(self.price_at(event.position()), event.position())

    def price_at(self, position):
        if not self.times or not self.plot_rects()[0].contains(position):
            return None
        self.fit_prices()
        value = Decimal(str(self.anchor(position)[1]))
        if not value.is_finite() or value <= 0:
            return None
        spec = self.service.specs.get(self.instrument, {}) if self.service else {}
        tick = Decimal(spec.get('tickSz') or '0.00000001')
        value = (value/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick
        return format(value, 'f') if value > 0 else None

    def copy_price(self, price, position):
        if price is not None:
            QApplication.clipboard().setText(price)
            QToolTip.showText(self.mapToGlobal(position.toPoint()), '已复制价格：'+price, self)

    def region_at(self, position):
        if not self.times or not self.plot_rects()[0].contains(position):
            return None
        self.fit_prices()
        lines = []
        for obj in self.objects:
            if obj.get('hidden') or self.bar not in obj.get('bars', BARS):
                continue
            points = [(p.x(), p.y()) for p in map(self.point, obj['anchors'])]
            a, b, tool = points[0], points[-1], obj['tool']
            if tool == 'horizontal':
                lines.append((a, (a[0]+1, a[1]), -math.inf, math.inf))
            elif tool == 'vertical':
                lines.append((a, (a[0], a[1]+1), -math.inf, math.inf))
            elif tool in ('trend', 'ray'):
                lines.append((a, b, 0, math.inf if tool == 'ray' else 1))
            elif tool == 'rectangle':
                corners = [a, (b[0], a[1]), b, (a[0], b[1])]
                lines.extend((p, q, 0, 1) for p, q in zip(corners, corners[1:]+corners[:1]))
        face = enclosed_region(lines, (position.x(), position.y()))
        return [self.anchor(QPointF(*p)) for p in face] if face else None

    def fill_region(self, anchors, context=None):
        context = context or (self.environment, self.instrument, self.bar)
        if context != (self.environment, self.instrument, self.bar):
            return
        chosen = QColorDialog.getColor(QColor('#4388ff'), self, '封闭区域填色（可在属性中调整透明度）')
        if not chosen.isValid() or context != (self.environment, self.instrument, self.bar):
            return
        obj = drawing('region', anchors)
        obj.update(color=chosen.name(), fill_color=chosen.name(), fill_opacity=20)
        self.objects.append(obj)
        self.selected_id = obj['id']
        self.persist_objects()

    def context_menu(self, position):
        self.cancel_pending_draw()
        identity, _ = self.hit_test(position)
        self.selected_id = identity
        menu = QMenu(self)
        price = self.price_at(position)
        if price is not None:
            environment, instrument = self.environment, self.instrument
            menu.addAction('复制价格', lambda: self.copy_price(price, position))
            if self.service:
                menu.addAction('提醒…', lambda: self.price_alert_requested.emit(environment, instrument, price))
                menu.addAction('多／空仓位测算…', lambda: self.risk_requested.emit(environment, instrument, price))
                orders = menu.addMenu('快捷下单')
                for direction, label in (('long', '限价开多…'), ('short', '限价开空…')):
                    orders.addAction(label, lambda checked=False, d=direction: self.order_requested.emit(environment, instrument, price, d))
            region = self.region_at(position)
            context = self.environment, self.instrument, self.bar
            fill = menu.addAction('封闭区域填色…', lambda: self.fill_region(region, context))
            fill.setEnabled(region is not None)
            menu.addSeparator()
        obj = self.selected()
        if obj:
            menu.addAction("编辑属性", lambda: self.edit_requested.emit(obj["id"]))
            def toggle():
                obj["locked"] = not obj["locked"]
                self.persist_objects()
            menu.addAction("解锁" if obj["locked"] else "锁定", toggle)
            action = menu.addAction("删除", self.delete_selected)
            action.setEnabled(not obj["locked"])
            menu.addSeparator()
        menu.addAction("恢复自动价格范围", self.auto)
        menu.addAction("返回最新 K 线", self.latest)
        return menu

    def contextMenuEvent(self, event):
        menu = self.context_menu(QPointF(event.pos()))
        menu.exec(event.globalPos())
        menu.deleteLater()

    def keyPressEvent(self, event):
        key, ctrl = event.key(), bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if key == Qt.Key.Key_Alt:
            self.refresh_drawing_pointer(Qt.KeyboardModifier.AltModifier)
        elif key == Qt.Key.Key_Escape:
            if self.drag and self.drag["mode"] == "object":
                before = self.drag["before"]
                self.objects = [deepcopy(before) if o["id"] == before["id"] else o for o in self.objects]
            self.drag = self.preview = None
            self.set_tool("cursor")
            self.tool_finished.emit()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif ctrl and key in (Qt.Key.Key_Z, Qt.Key.Key_Y) and self.book:
            self.cancel_pending_draw()
            self.book.undo(self.environment, self.instrument, key == Qt.Key.Key_Y or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Alt:
            self.refresh_drawing_pointer(event.modifiers() & ~Qt.KeyboardModifier.AltModifier)
        else:
            super().keyReleaseEvent(event)

    def leaveEvent(self, event):
        self.pointer = None
        self.snap_target = None
        self.crosshair_changed.emit(None)
        self.update()

    def hideEvent(self, event):
        self.cancel_pending_draw()
        self.frame_timer.stop()
        self.flush_view()
        super().hideEvent(event)
