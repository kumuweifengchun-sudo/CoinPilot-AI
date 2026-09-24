"""画布绘制与命中区域；绘制工作量只与可见蜡烛及绘图对象有关。"""
from datetime import datetime
from bisect import bisect_left

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen

from .chart_state import BARS
from ..theme import color


class ChartRenderer:
    def object_path(self, obj):
        main, _ = self.plot_rects()
        points = [self.point(a) for a in obj["anchors"]]
        a, b = points[0], points[-1]
        path, labels = QPainterPath(), []
        def line(start, end):
            path.moveTo(start)
            path.lineTo(end)
        tool = obj["tool"]
        if tool == "horizontal":
            line(QPointF(main.left(), a.y()), QPointF(main.right(), a.y()))
        elif tool == "vertical":
            line(QPointF(a.x(), main.top()), QPointF(a.x(), main.bottom()))
        elif tool == "rectangle":
            path.addRect(QRectF(a, b).normalized())
        elif tool == "text":
            path.addRect(QRectF(a.x(), a.y()-18, max(35, self.fontMetrics().horizontalAdvance(obj["text"])+8), 23))
        elif tool == "fib":
            for level in obj["levels"]:
                price = obj["anchors"][1][1] + (obj["anchors"][0][1]-obj["anchors"][1][1])*level["value"]
                y = self.y(price)
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

    def draw_objects(self, p):
        self.hit_paths = {}
        p.save()
        main, _ = self.plot_rects()
        p.setClipRect(main)
        for obj in self.objects + ([self.preview] if self.preview else []):
            if obj.get("hidden") or self.bar not in obj.get("bars", BARS):
                continue
            path, points, labels = self.object_path(obj)
            if not path.boundingRect().adjusted(-120, -30, 120, 30).intersects(main):
                continue
            style = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine}[obj["style"]]
            p.setPen(QPen(QColor(obj["color"]), obj["width"], style))
            if obj["tool"] == "text":
                p.drawText(path.boundingRect(), Qt.AlignmentFlag.AlignLeft, obj["text"])
            elif obj["tool"] == "fib":
                a, b = points
                for level in obj["levels"]:
                    y = self.y(obj["anchors"][1][1]+(obj["anchors"][0][1]-obj["anchors"][1][1])*level["value"])
                    p.setPen(QPen(QColor(level["color"]), obj["width"], style))
                    p.drawLine(QPointF(min(a.x(), b.x()), y), QPointF(max(a.x(), b.x()), y))
            else:
                p.drawPath(path)
                if obj["tool"] == "rectangle":
                    fill = QColor(obj["color"])
                    fill.setAlpha(24)
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
            self.hit_paths[obj["id"]] = path if obj["tool"] == "text" else stroker.createStroke(path)
            if obj["id"] == self.selected_id:
                p.setBrush(QColor(color("surface")))
                p.setPen(QPen(QColor(color("text_secondary" if not obj["locked"] else "text_muted")), 1))
                for point in points:
                    p.drawEllipse(point, 4, 4)
                p.setBrush(Qt.BrushStyle.NoBrush)
        p.restore()

    def price_label(self, p, price, text, label_color, *, line=True):
        main, _ = self.plot_rects()
        y = self.y(price)
        if not main.top() <= y <= main.bottom():
            return
        if line:
            p.setPen(QPen(QColor(label_color), .8, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(main.left(), y), QPointF(main.right(), y))
        p.fillRect(QRectF(main.right()+1, y-9, 84, 19), QColor(label_color))
        p.setPen(QColor(color("accent_text")))
        p.drawText(QRectF(main.right()+3, y-9, 81, 19), Qt.AlignmentFlag.AlignCenter, text)

    def paintEvent(self, event):
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
        cursor = self.point(self.snap_target[:2]) if self.snap_target is not None else self.pointer
        hovered = len(self.rows)-1
        if cursor and main.left() <= cursor.x() <= main.right():
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
                label = datetime.fromtimestamp(self.time_at(index)/1000).strftime("%m-%d %H:%M")
            except (ValueError, OSError, OverflowError):
                label = "—"
            p.setPen(QPen(QColor(color("chart_grid")), .7))
            p.drawLine(QPointF(x, main.top()), QPointF(x, volume.bottom()))
            p.setPen(QColor(color("chart_axis")))
            p.drawText(QRectF(x, volume.bottom()+4, 100, 22), Qt.AlignmentFlag.AlignLeft, label)
        p.setPen(QPen(QColor(color("border")), 1))
        p.drawLine(QPointF(main.left(), volume.top()-4), QPointF(main.right(), volume.top()-4))
        max_volume = max((v[4] for v in self.values[start:end]), default=1) or 1
        p.save()
        p.setClipRect(main)
        for i in range(start, end):
            op, high, low, close, vol = self.values[i]
            x = self.x(self.times[i])
            candle_color = QColor(color("positive" if close >= op else "negative"))
            p.setPen(QPen(candle_color, 1))
            p.drawLine(QPointF(x, self.y(high)), QPointF(x, self.y(low)))
            p.fillRect(QRectF(x-step*.32, min(self.y(op), self.y(close)), max(1, step*.64), max(1, abs(self.y(op)-self.y(close)))), candle_color)
        p.setClipRect(volume)
        for i in range(start, end):
            op, high, low, close, vol = self.values[i]
            x = self.x(self.times[i])
            candle_color = QColor(color("volume_up" if close >= op else "volume_down"))
            candle_color.setAlpha(140)
            height = vol/max_volume*volume.height()
            p.fillRect(QRectF(x-step*.32, volume.bottom()-height, max(1, step*.64), height), candle_color)
        p.setClipRect(main)
        for setting, values in zip(self.emas, self.ema_cache):
            if not setting["visible"]:
                continue
            path = QPainterPath()
            for i in range(start, end):
                point = QPointF(self.x(self.times[i]), self.y(values[i]))
                if i == start or self.times[i]-self.times[i-1] > self.interval*1.1:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            p.setPen(QPen(QColor(setting["color"]), setting["width"]))
            p.drawPath(path)
        p.restore()
        self.draw_objects(p)
        if self.last_price:
            self.price_label(p, self.last_price, f"{self.last_price:,.7g}", color("text_muted" if self.price_stale else "positive"))
        self.draw_overlays(p, main)
        if cursor and main.left() <= cursor.x() <= main.right() and main.top() <= cursor.y() <= volume.bottom():
            x = cursor.x()
            p.setPen(QPen(QColor(color("chart_crosshair")), .8, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, main.top()), QPointF(x, volume.bottom()))
            if main.contains(cursor):
                price = self.snap_target[1] if self.snap_target is not None else self.anchor(cursor)[1]
                self.price_label(p, price, f"{price:,.7g}", color("border_strong"))
            date = datetime.fromtimestamp(self.times[hovered]/1000).strftime("%Y-%m-%d %H:%M")
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
        visible = [o for o in sorted(self.overlays, key=lambda o: -o["price"]) if main.top() <= self.y(o["price"]) <= main.bottom()]
        # 空间不足时聚合多余标签，价格线仍完整显示。
        capacity = max(1, int(main.height()/22))
        label_rows = visible[:capacity]
        positions = []
        for o in label_rows:
            positions.append(max(self.y(o["price"]), positions[-1]+21 if positions else main.top()+10))
        if positions and positions[-1] > main.bottom()-10:
            positions[-1] = main.bottom()-10
            for i in range(len(positions)-2, -1, -1):
                positions[i] = min(positions[i], positions[i+1]-21)
        for i, overlay in enumerate(visible):
            y = self.y(overlay["price"])
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
