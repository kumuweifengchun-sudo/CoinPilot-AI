"""与主图共用时间视口的轻量指标副图。"""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from coinpilot_ai.ui.theme import color


class IndicatorStrip(QWidget):
    def __init__(self, canvas, indicator_id, parent=None):
        super().__init__(parent)
        self.canvas, self.indicator_id = canvas, indicator_id
        self.setFixedHeight(112)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(color("chart_background")))
        item = next(((setting, result) for setting, result in self.canvas.indicator_results
                     if setting["id"] == self.indicator_id), None)
        if item is None or not self.canvas.times:
            return
        setting, result = item
        p.setPen(QColor(color("text_secondary")))
        p.drawText(8, 15, setting["kind"] + " · " + ", ".join(str(v) for v in setting["params"].values()))
        left, right, top, bottom = 45., self.width()-90., 22., self.height()-8.
        if right <= left:
            return
        start, end = self.canvas.visible()
        sequences = list(result.lines.values())
        numbers = [v for values in sequences for v in values[start:end] if v is not None]
        if not numbers:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "历史预热不足")
            return
        minimum, maximum = min(numbers), max(numbers)
        if setting["kind"] in ("RSI", "STOCHASTIC"):
            minimum, maximum = 0., 100.
        if maximum == minimum:
            maximum, minimum = maximum+1, minimum-1
        p.setClipRect(QRectF(left, top, right-left, bottom-top))
        for index, values in enumerate(sequences):
            path = QPainterPath()
            active = False
            for i in range(start, min(end, len(values))):
                value = values[i]
                if value is None:
                    active = False
                    continue
                x = left + (self.canvas.times[i]-self.canvas.left_time)/self.canvas.interval / self.canvas.count * (right-left)
                y = bottom - (value-minimum)/(maximum-minimum)*(bottom-top)
                if not active or i == 0 or self.canvas.times[i]-self.canvas.times[i-1] > self.canvas.interval*1.1:
                    path.moveTo(QPointF(x, y))
                else:
                    path.lineTo(QPointF(x, y))
                active = True
            shade = QColor(setting["color"])
            shade.setAlpha(max(90, 255-index*65))
            p.setPen(QPen(shade, setting["width"]))
            p.drawPath(path)
        p.setClipping(False)
        p.setPen(QColor(color("chart_axis")))
        p.drawText(QRectF(right+4, top, 80, 16), Qt.AlignmentFlag.AlignLeft, f"{maximum:.5g}")
        p.drawText(QRectF(right+4, bottom-16, 80, 16), Qt.AlignmentFlag.AlignLeft, f"{minimum:.5g}")
