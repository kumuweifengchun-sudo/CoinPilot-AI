"""画布绘制与命中区域；绘制工作量只与可见蜡烛及绘图对象有关。"""
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .canvas import CandleChart

from datetime import datetime
from math import ceil
from bisect import bisect_left, bisect_right

from PyQt6.QtCore import QLineF, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap

from coinpilot_ai.market.intervals import BARS, contiguous
from coinpilot_ai.market.lod import pixel_buckets
from coinpilot_ai.market.buffer import ValueView
from coinpilot_ai.ui.theme import color
from .position import POSITION_TOOLS, position_metrics
from .pattern import projected_prices


type LayerKey = tuple[int, int, float, str, int, str, float | None, float, float, float, float, int]
type MarketLayerKey = tuple[LayerKey, int, tuple[tuple[tuple[str, object], ...], ...], float]
type ObjectLayerKey = tuple[LayerKey, str | None, str]


class ChartRenderer:
    """CandleChart 的绘制混入，使用画布提供的几何和状态。"""

    def draw_pattern_selection(self, p, main):
        self = cast("CandleChart", self)
        selection = self.pattern_selection
        if selection is None or not self.times:
            return
        first, last = sorted(selection)
        step = main.width()/self.count
        left = self.time_x(first)-step*.5
        right = self.time_x(last)+step*.5
        shade = QColor(color("focus"))
        shade.setAlpha(45)
        p.save()
        p.setClipRect(main)
        p.fillRect(QRectF(left, main.top(), right-left, main.height()), shade)
        p.setPen(QPen(QColor(color("focus")), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(left, main.top()), QPointF(left, main.bottom()))
        p.drawLine(QPointF(right, main.top()), QPointF(right, main.bottom()))
        p.restore()

    def draw_pattern_object(self, p, obj, main):
        self = cast("CandleChart", self)
        try:
            start, anchor_price = obj["anchors"][0]
            prices = projected_prices(obj["closes"], float(anchor_price))
            interval = int(obj["interval"])
            if interval <= 0:
                return
        except (KeyError, IndexError, TypeError, ValueError):
            return
        path = QPainterPath()
        for index, price in enumerate(prices):
            point = QPointF(self.time_x(start+index*interval), self.price_y(price))
            if index:
                path.lineTo(point)
            else:
                path.moveTo(point)
        if not path.boundingRect().adjusted(-10, -10, 10, 10).intersects(main):
            return
        shade = QColor(obj["color"])
        shade.setAlpha(round(255*max(0, min(100, int(obj.get("opacity", 75))))/100))
        style = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine,
                 "dot": Qt.PenStyle.DotLine}.get(obj.get("style"), Qt.PenStyle.SolidLine)
        p.setPen(QPen(shade, obj["width"], style))
        p.drawPath(path)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(10, obj["width"]+6))
        self.hit_paths[obj["id"]] = stroker.createStroke(path)
        if obj["id"] == self.selected_id:
            p.setBrush(QColor(color("surface")))
            p.setPen(QPen(QColor(color("text_secondary")), 1))
            p.drawEllipse(QPointF(self.time_x(start), self.price_y(anchor_price)), 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)

    def object_path(self, obj):
        self = cast("CandleChart", self)
        main, _ = self.plot_rects()
        points = [self.point(a) for a in obj["anchors"]]
        a, b = points[0], points[-1]
        path, labels = QPainterPath(), []
        def line(start, end):
            path.moveTo(start)
            path.lineTo(end)
        tool = obj["tool"]
        if tool in POSITION_TOOLS:
            entry, target, stop = (anchor[1] for anchor in obj['anchors'])
            x0, x1 = sorted((self.time_x(obj['anchors'][0][0]), self.time_x(obj['anchors'][1][0])))
            for price in (entry, target, stop):
                line(QPointF(x0, self.price_y(price)), QPointF(x1, self.price_y(price)))
            line(QPointF(x1, min(self.price_y(target), self.price_y(stop))),
                 QPointF(x1, max(self.price_y(target), self.price_y(stop))))
        elif tool == "horizontal":
            line(QPointF(main.left(), a.y()), QPointF(main.right(), a.y()))
        elif tool == "vertical":
            line(QPointF(a.x(), main.top()), QPointF(a.x(), main.bottom()))
        elif tool == "rectangle":
            path.addRect(QRectF(a, b).normalized())
        elif tool == "region":
            path.moveTo(a)
            for point in points[1:]:
                path.lineTo(point)
            path.closeSubpath()
        elif tool == "text":
            path.addRect(QRectF(a.x(), a.y()-18, max(35, self.fontMetrics().horizontalAdvance(obj["text"])+8), 23))
        elif tool == "fib":
            for level in obj["levels"]:
                price = obj["anchors"][1][1] + (obj["anchors"][0][1]-obj["anchors"][1][1])*level["value"]
                y = self.price_y(price)
                line(QPointF(min(a.x(), b.x()), y), QPointF(max(a.x(), b.x()), y))
                labels.append((min(a.x(), b.x())+4, y-3, f"{level['label']}  ({price:,.6g})", level["color"]))
        else:
            if tool == "ray":
                delta = b-a
                if abs(delta.x()) > .001:
                    edge = main.right() if delta.x() > 0 else main.left()
                    b = a + delta*((edge-a.x())/delta.x())
                elif abs(delta.y()) > .001:
                    b = QPointF(a.x(), main.bottom() if delta.y() > 0 else main.top())
            line(a, b)
            if tool == "measure":
                first, last = obj["anchors"]
                diff = last[1]-first[1]
                pct = diff/first[1]*100 if first[1] else 0
                minutes = abs(last[0]-first[0])/60000
                n = abs(self.index_at(last[0])-self.index_at(first[0]))
                labels.append((min(a.x(), b.x())+4, min(a.y(), b.y())-8,
                               f"{diff:+,.6g} ({pct:+.2f}%) · {n:.1f} 根 · {minutes:g} 分钟", obj["color"]))
        return path, points, labels

    def draw_objects(self, p, objects):
        self = cast("CandleChart", self)
        p.save()
        main, _ = self.plot_rects()
        p.setClipRect(main)
        for obj in sorted(objects, key=lambda item: item['tool'] != 'region'):
            if obj.get("hidden") or self.bar not in obj.get("bars", BARS):
                continue
            if obj["tool"] == "price_pattern":
                self.draw_pattern_object(p, obj, main)
                continue
            path, points, labels = self.object_path(obj)
            if not path.boundingRect().adjusted(-120, -30, 120, 30).intersects(main):
                continue
            if obj['tool'] in POSITION_TOOLS:
                self.draw_position_object(p, obj, path, main)
                continue
            style = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine}[obj["style"]]
            p.setPen(QPen(QColor(obj["color"]), obj["width"], style))
            if obj["tool"] == "text":
                p.drawText(path.boundingRect(), Qt.AlignmentFlag.AlignLeft, obj["text"])
            elif obj["tool"] == "fib":
                a, b = points
                for level in obj["levels"]:
                    y = self.price_y(obj["anchors"][1][1]+(obj["anchors"][0][1]-obj["anchors"][1][1])*level["value"])
                    p.setPen(QPen(QColor(level["color"]), obj["width"], style))
                    p.drawLine(QPointF(min(a.x(), b.x()), y), QPointF(max(a.x(), b.x()), y))
            else:
                p.drawPath(path)
                if obj["tool"] in ("rectangle", "region"):
                    fill = QColor(obj.get("fill_color", obj["color"]))
                    fill.setAlpha(round(255*obj.get("fill_opacity", 24/255*100)/100))
                    p.fillPath(path, fill)
            last_label = -1e9
            for x, y, text, label_color in sorted(labels, key=lambda item: item[1]):
                if y < main.top()-12 or y > main.bottom()+12:
                    continue
                y = max(main.top()+12, min(main.bottom()-3, y))
                if y-last_label < 14:
                    continue  # 密集时保留比例线，避免标签互相覆盖；放大后恢复全部标签。
                last_label = y
                p.setPen(QColor(label_color))
                p.drawText(QPointF(x, y), text)
            stroker = QPainterPathStroker()
            stroker.setWidth(max(10, obj["width"]+6))
            self.hit_paths[obj["id"]] = path if obj["tool"] in ("text", "region") else stroker.createStroke(path)
            if obj["id"] == self.selected_id:
                p.setBrush(QColor(color("surface")))
                p.setPen(QPen(QColor(color("text_secondary" if not obj["locked"] else "text_muted")), 1))
                for point in points:
                    p.drawEllipse(point, 4, 4)
                p.setBrush(Qt.BrushStyle.NoBrush)
        p.restore()

    def draw_position_object(self, p, obj, path, main):
        self = cast("CandleChart", self)
        start = self.time_x(obj['anchors'][0][0])
        end = self.time_x(obj['anchors'][1][0])
        left, right = sorted((start, end))
        entry, target, stop = (self.price_y(anchor[1]) for anchor in obj['anchors'])
        gain, loss = QColor(color('positive')), QColor(color('negative'))
        gain.setAlpha(42)
        loss.setAlpha(42)
        p.fillRect(QRectF(left, min(entry, target), right-left, abs(entry-target)), gain)
        p.fillRect(QRectF(left, min(entry, stop), right-left, abs(entry-stop)), loss)
        p.setPen(QPen(QColor(color('positive')), 1.3))
        p.drawLine(QPointF(left, target), QPointF(right, target))
        p.setPen(QPen(QColor(color('text')), 1.3))
        p.drawLine(QPointF(left, entry), QPointF(right, entry))
        p.setPen(QPen(QColor(color('negative')), 1.3))
        p.drawLine(QPointF(left, stop), QPointF(right, stop))
        p.setPen(QPen(QColor(obj['color']), obj['width']))
        p.drawLine(QPointF(right, min(target, stop)), QPointF(right, max(target, stop)))
        try:
            result = position_metrics(obj)
        except ValueError:
            result = None  # 未完成绘制或拖动经过无效价格时，仅预览图形。
        if result:
            labels = ((target, entry, f"预计盈利 +{result['profit_usdt']:,.2f} U · {result['profit_percent']:.2f}%", 'positive'),
                      (stop, entry, f"预计亏损 -{result['loss_usdt']:,.2f} U · {result['loss_percent']:.2f}%", 'negative'))
            for boundary, middle, text, token in labels:
                y = (boundary+middle)/2
                if not main.top()+10 <= y <= main.bottom()-4 or right-left < 55:
                    continue
                available = min(main.right(), right)-max(main.left(), left)-8
                if available < 45:
                    continue
                metrics = p.fontMetrics()
                shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(available))
                x = max(main.left()+4, min(left+5, main.right()-available))
                p.fillRect(QRectF(x-2, y-11, available+4, 19), QColor(color('surface_raised')))
                p.setPen(QColor(color(token)))
                p.drawText(QPointF(x, y+3), shown)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(10, obj['width']+6))
        hit = stroker.createStroke(path)
        hit.addRect(QRectF(left, min(target, stop), right-left, abs(target-stop)))
        self.hit_paths[obj['id']] = hit
        if obj['id'] == self.selected_id:
            p.setBrush(QColor(color('surface')))
            p.setPen(QPen(QColor(color('text_muted' if obj['locked'] else 'text_secondary')), 1))
            for point in (self.point(anchor) for anchor in obj['anchors']):
                p.drawEllipse(point, 4, 4)
            p.drawEllipse(QPointF(right, entry), 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)

    def price_label(self, p, price, text, label_color, *, line=True):
        self = cast("CandleChart", self)
        main, _ = self.plot_rects()
        y = self.price_y(price)
        if not main.top() <= y <= main.bottom():
            return
        if line:
            p.setPen(QPen(QColor(label_color), .8, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(main.left(), y), QPointF(main.right(), y))
        p.fillRect(QRectF(main.right()+1, y-9, 84, 19), QColor(label_color))
        p.setPen(QColor(color("accent_text")))
        p.drawText(QRectF(main.right()+3, y-9, 81, 19), Qt.AlignmentFlag.AlignCenter, text)

    def layer_key(self) -> LayerKey:
        self = cast("CandleChart", self)
        return (self.width(), self.height(), self.devicePixelRatioF(), self.font().key(),
                self._theme_revision, self.bar, self.left_time, self.count,
                self.low, self.high, self.volume_ratio, self.render_interval)

    def new_layer(self):
        self = cast("CandleChart", self)
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(max(1, round(self.width()*ratio)), max(1, round(self.height()*ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap

    def draw_market_layer(self, painter):
        self = cast("CandleChart", self)
        main, volume = self.plot_rects()
        start, end = self.visible()
        maximum = max((self.values[i][4] for i in range(start, end)), default=1) or 1
        layer = self.layer_key()
        key = (layer, self._plot_revision, tuple(tuple(e.items()) for e in self.emas), maximum)
        self.draw_axes(painter)
        if key != self._market_key:
            previous = self._market_key
            delta = 0
            if previous is not None and previous[1:] == key[1:]:
                old = previous[0]
                if (old[6] is not None and layer[6] is not None and
                        main.width()/self.count*self.render_interval/self.interval >= 1 and
                        old[:6] == layer[:6] and old[7:] == layer[7:]):
                    pixels = (old[6]-layer[6])/(self.count*self.interval)*main.width()*self.devicePixelRatioF()
                    if abs(pixels-round(pixels)) < 1e-6 and 0 < abs(pixels) < main.width()*self.devicePixelRatioF()/2:
                        delta = round(pixels)
            clip = None
            if delta:
                ratio = self.devicePixelRatioF()
                region = QRect(round(main.left()*ratio), round(main.top()*ratio),
                               round(main.width()*ratio), round((volume.bottom()-main.top()+1)*ratio))
                self._market_layer.scroll(delta, 0, region)
                width = abs(delta)+ceil(3*ratio)
                # 暴露条带也对齐设备像素；否则 125%/150% DPI 在接缝留下半透明像素。
                edge = region.x() if delta > 0 else region.x()+region.width()-width
                clip = QRectF(edge/ratio, region.y()/ratio, width/ratio, region.height()/ratio)
                self.market_scrolls = getattr(self, "market_scrolls", 0)+1
            else:
                self._market_layer = self.new_layer()
            p = QPainter(self._market_layer)
            p.setFont(self.font())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if clip is not None:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.fillRect(clip, Qt.GlobalColor.transparent)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                p.setClipRect(clip)
            self.draw_market(p, clip=clip, maximum=maximum)
            p.end()
            self._market_key = key
            self.market_builds += 1
        painter.drawPixmap(0, 0, self._market_layer)

    def draw_axes(self, p):
        self = cast("CandleChart", self)
        main, volume = self.plot_rects()
        left, step = self.left_index(), main.width()/self.count
        for i in range(6):
            y = main.top()+main.height()*i/5
            price = self.high-(self.high-self.low)*i/5
            p.setPen(QPen(QColor(color("chart_grid")), .7))
            p.drawLine(QPointF(main.left(), y), QPointF(main.right(), y))
            p.setPen(QColor(color("chart_axis")))
            p.drawText(QRectF(main.right()+6, y-9, 80, 20), Qt.AlignmentFlag.AlignVCenter, f"{price:,.7g}")
        grid_count = max(2, int(main.width()/135))
        for i in range(grid_count):
            index = left+i*self.count/grid_count
            x = main.left()+(index-left)*step
            try:
                format_ = "%H:%M:%S" if self.interval < 60000 else ("%Y-%m-%d" if self.interval >= 86400000 else "%m-%d %H:%M")
                label = datetime.fromtimestamp(self.time_at(index)/1000).strftime(format_)
            except (ValueError, OSError, OverflowError):
                label = "—"
            p.setPen(QPen(QColor(color("chart_grid")), .7))
            p.drawLine(QPointF(x, main.top()), QPointF(x, volume.bottom()))
            p.setPen(QColor(color("chart_axis")))
            p.drawText(QRectF(x, volume.bottom()+4, 100, 22), Qt.AlignmentFlag.AlignLeft, label)
        p.setPen(QPen(QColor(color("border")), 1))
        p.drawLine(QPointF(main.left(), volume.top()-4), QPointF(main.right(), volume.top()-4))
    def draw_market(self, p, *, clip=None, maximum=None):
        self = cast("CandleChart", self)
        main, volume = self.plot_rects()
        start, end = self.visible()
        if clip is not None:
            span = self.count*self.interval
            first = self.left_time+(clip.left()-main.left())/main.width()*span
            last = self.left_time+(clip.right()-main.left())/main.width()*span
            start = max(start, bisect_left(self.times, first)-2)
            end = min(end, bisect_right(self.times, last)+2)
        step = main.width()/self.count*self.render_interval/self.interval
        visible = self.values[start:end]
        max_volume = maximum or max((v[4] for v in visible), default=1) or 1
        sy = main.height()/max(1e-14, self.high-self.low)
        bottom, low = main.bottom(), self.low
        interval, volume_bottom, volume_height = self.render_interval, volume.bottom(), volume.height()
        ratio = self.devicePixelRatioF()
        body_width = max(1, step*.64)
        source_times = self.times[start:end]
        xs = [main.left()+(stamp-self.left_time)/interval*step+step*.5 for stamp in source_times]
        candle_xs = xs
        if step < 1:
            grouped = pixel_buckets(self.rows[start:end], self.left_time, self.count*self.interval,
                                     main.width(), interval, bar=self.render_bar)
            visible = ValueView(grouped)
            candle_xs = [main.left()+(stamp-self.left_time)/interval*step+.5 for stamp in grouped.times]
            max_volume = max((row[5] for row in grouped), default=1) or 1
        self.rendered_candles = len(candle_xs)
        wicks = [[], []]
        bodies = [[], []]
        volumes = [[], []]
        for x, (op, high, lo, close, vol) in zip(candle_xs, visible):
            side = int(close >= op)
            yo, yc = bottom-(op-low)*sy, bottom-(close-low)*sy
            wicks[side].append(QLineF(x, bottom-(high-low)*sy, x, bottom-(lo-low)*sy))
            bodies[side].append(QRectF(x-step*.32, min(yo, yc), body_width, max(1, abs(yo-yc))))
            height = vol/max_volume*volume_height
            # 半透明粗笔在非整数 DPI 下会退化为昂贵的路径光栅化。
            # 仅在光栅层把同一根柱分为设备像素宽的细线，逻辑坐标/命中不变。
            pixel_left = round((x-step*.32)*ratio)
            pixel_right = max(pixel_left+1, round((x-step*.32+body_width)*ratio))
            for pixel in range(pixel_left, pixel_right):
                center = (pixel+.5)/ratio
                volumes[side].append(QLineF(center, volume_bottom-height, center, volume_bottom))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for side, token in enumerate(("negative", "positive")):
            p.save()
            p.setClipRect(main, Qt.ClipOperation.IntersectClip)
            shade = QColor(color(token))
            p.setPen(QPen(shade, 1))
            p.drawLines(wicks[side])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(shade)
            p.drawRects(bodies[side])
            p.restore()
            p.save()
            p.setClipRect(volume, Qt.ClipOperation.IntersectClip)
            shade = QColor(color("volume_up" if side else "volume_down"))
            shade.setAlpha(140)
            p.setPen(QPen(shade, 0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawLines(volumes[side])
            p.restore()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setClipRect(main, Qt.ClipOperation.IntersectClip)
        for setting, values in zip(self.emas, self.ema_cache):
            if not setting["visible"]:
                continue
            if any(item["kind"] == "EMA" and item["params"]["period"] == setting["period"]
                   and item["color"] == setting["color"] and item["width"] == setting["width"]
                   for item, _ in self.indicator_results):
                continue
            path = QPainterPath()
            for offset, x in enumerate(xs):
                i = start+offset
                y = bottom-(values[i]-low)*sy
                if i == start or not contiguous(self.times[i-1], self.times[i], self.render_bar):
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(QColor(setting["color"]), setting["width"]))
            p.drawPath(path)
        for setting, result in self.indicator_results:
            if setting["kind"] not in ("MA", "EMA", "BOLL", "VWAP", "SUPERTREND"):
                continue
            for values in result.lines.values():
                path = QPainterPath()
                active = False
                for offset, x in enumerate(xs):
                    i = start+offset
                    value = values[i] if i < len(values) else None
                    if value is None:
                        active = False
                        continue
                    y = bottom-(value-low)*sy
                    if not active or i == 0 or not contiguous(self.times[i-1], self.times[i], self.render_bar):
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
                    active = True
                p.setPen(QPen(QColor(setting["color"]), setting["width"]))
                p.drawPath(path)
        p.restore()

    def object_layers(self):
        self = cast("CandleChart", self)
        dragged = self.selected_id if self.drag and self.drag["mode"] == "object" else None
        fixed = [o for o in self.objects if o["id"] != dragged]
        live = [o for o in self.objects if o["id"] == dragged]
        if self.preview:
            live.append(self.preview)
        # 对象数远小于历史数；签名也覆盖外部属性编辑与撤销后的原地变更。
        key = (self.layer_key(), self.selected_id, repr(fixed))
        if key != self._objects_key:
            self._objects_layer = self.new_layer()
            p = QPainter(self._objects_layer)
            p.setFont(self.font())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.hit_paths = {}
            self.draw_objects(p, fixed)
            p.end()
            self._fixed_hit_paths = dict(self.hit_paths)
            self._objects_key = key
            self.object_builds += 1
        self.hit_paths = dict(self._fixed_hit_paths)
        return live

    def draw_object_layer(self, painter):
        self = cast("CandleChart", self)
        live = self.object_layers()
        painter.drawPixmap(0, 0, self._objects_layer)
        self.draw_objects(painter, live)

    def paintEvent(self, a0):
        self = cast("CandleChart", self)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(color("chart_background")))
        p.setPen(QColor(color("text")))
        p.drawText(12, 20, self.title)
        if not self.values:
            p.setPen(QColor(color("text_muted")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "等待 OKX K 线数据")
            return
        self.fit_prices()
        main, volume = self.plot_rects()
        start, end = self.visible()
        left, step = self.left_index(), main.width()/self.count
        self.draw_market_layer(p)
        self.draw_pattern_selection(p, main)
        if start >= end:
            self.draw_object_layer(p)
            p.setPen(QColor(color("text_muted")))
            p.drawText(main, Qt.AlignmentFlag.AlignCenter, "当前时间范围暂无 K 线")
            p.end()
            return
        cursor = self.point(self.snap_target[:2]) if self.snap_target is not None else self.pointer
        hovered = len(self.rows)-1
        if cursor is not None and main.left() <= cursor.x() <= main.right():
            stamp = self.time_at(left+(cursor.x()-main.left())/step-.5)
            index = bisect_left(self.times, stamp)
            hovered = min(range(max(0, index-1), min(len(self.times), index+1)), key=lambda i: abs(self.times[i]-stamp))
        op, high, low, close, vol = self.values[hovered]
        header = f"开 {op:,.6g}  高 {high:,.6g}  低 {low:,.6g}  收 {close:,.6g}  量 {vol:,.6g} 张"
        p.setPen(QColor(color("positive" if close >= op else "negative")))
        p.drawText(QRectF(230, 4, max(0, self.width()-240), 23), Qt.AlignmentFlag.AlignRight, header)
        xlegend = 12.
        p.save()
        p.setClipRect(QRectF(8, 24, main.width()-155, 27))
        for setting, values in zip(self.emas, self.ema_cache):
            if setting["visible"]:
                text = f"EMA {setting['period']}  {values[hovered]:,.6g}"
                if len(self.rows) < setting["period"]*5:
                    text += " *"
                p.setPen(QColor(setting["color"]))
                p.drawText(QPointF(xlegend, 42), text)
                xlegend += p.fontMetrics().horizontalAdvance(text)+20
        p.restore()
        p.setPen(QColor(color("text_muted")))
        if any(len(self.rows) < e["period"]*5 for e in self.emas if e["visible"]):
            p.drawText(QRectF(main.right()-152, 26, 150, 20), Qt.AlignmentFlag.AlignRight, "* 历史预热不足")
        self.draw_object_layer(p)
        if self.last_price:
            # 与最新 K 线的开盘价比较，不受鼠标悬停或历史视口影响。
            direction = "positive" if self.last_price >= self.values[-1][0] else "negative"
            self.price_label(p, self.last_price, f"{self.last_price:,.7g}",
                             color("text_muted" if self.price_stale else direction))
        self.draw_overlays(p, main)
        if self.external_crosshair_time is not None:
            external_x = self.time_x(self.external_crosshair_time)
            if main.left() <= external_x <= main.right():
                p.setPen(QPen(QColor(color("chart_crosshair")), .8, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(external_x, main.top()), QPointF(external_x, volume.bottom()))
        if cursor is not None and main.left() <= cursor.x() <= main.right() and main.top() <= cursor.y() <= volume.bottom():
            x = cursor.x()
            p.setPen(QPen(QColor(color("chart_crosshair")), .8, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, main.top()), QPointF(x, volume.bottom()))
            if main.contains(cursor):
                price = self.snap_target[1] if self.snap_target is not None else self.anchor(cursor)[1]
                self.price_label(p, price, f"{price:,.7g}", color("border_strong"))
            date = datetime.fromtimestamp(self.times[hovered]/1000).strftime("%Y-%m-%d %H:%M:%S" if self.interval < 60000 else "%Y-%m-%d %H:%M")
            x = max(main.left(), min(x-62, main.right()-136))
            p.fillRect(QRectF(x, volume.bottom()+1, 136, 24), QColor(color("surface_selected")))
            p.setPen(QColor(color("text")))
            p.drawText(QRectF(x, volume.bottom()+1, 136, 24), Qt.AlignmentFlag.AlignCenter, date)
        if self.snap_target is not None:
            stamp, price, kind = self.snap_target
            target = self.point([stamp, price])
            if main.contains(target):
                p.save()
                p.setClipRect(main)
                p.setBrush(QColor(color("surface_raised")))
                p.setPen(QPen(QColor(color("positive")), 1.5))
                p.drawEllipse(target, 6, 6)
                text = f"磁吸 · {kind} {price:,.10g}"
                width = min(main.width()-8, p.fontMetrics().horizontalAdvance(text)+14)
                label = QRectF(max(main.left()+4, min(target.x()+12, main.right()-width-4)),
                               max(main.top()+4, target.y()-28), width, 22)
                p.drawRoundedRect(label, 4, 4)
                p.drawText(label, Qt.AlignmentFlag.AlignCenter, text)
                p.restore()
        p.end()

    def draw_overlays(self, p, main):
        self = cast("CandleChart", self)
        visible = [o for o in sorted(self.overlays, key=lambda o: -o["price"]) if main.top() <= self.price_y(o["price"]) <= main.bottom()]
        # 空间不足时聚合多余标签，价格线仍完整显示。
        capacity = max(1, int(main.height()/22))
        label_rows = visible[:capacity]
        positions = []
        for o in label_rows:
            positions.append(max(self.price_y(o["price"]), positions[-1]+21 if positions else main.top()+10))
        if positions and positions[-1] > main.bottom()-10:
            positions[-1] = main.bottom()-10
            for i in range(len(positions)-2, -1, -1):
                positions[i] = min(positions[i], positions[i+1]-21)
        for i, overlay in enumerate(visible):
            y = self.price_y(overlay["price"])
            overlay_color = color("text_muted") if self.account_stale else overlay["color"]
            p.setPen(QPen(QColor(overlay_color), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(main.left(), y), QPointF(main.right(), y))
            if i >= len(positions):
                continue
            text = overlay["label"]+f" · {overlay['price']:,.7g}"+(" · 账户过期" if self.account_stale else "")
            if i == capacity-1 and len(visible) > capacity:
                text += f" · 另 {len(visible)-capacity} 项见列表"
            width = min(main.width()-12, p.fontMetrics().horizontalAdvance(text)+12)
            label_y = positions[i]
            p.fillRect(QRectF(main.right()-width-4, label_y-10, width, 20), QColor(color("surface_raised")))
            p.drawText(QRectF(main.right()-width, label_y-10, width-4, 20), Qt.AlignmentFlag.AlignVCenter, text)
