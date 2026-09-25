"""按图表时间视口绘制公开衍生品序列。"""
import time
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from coinpilot_ai.ui.theme import color


class DerivativeStrip(QWidget):
    def __init__(self, panel, kind, parent=None):
        super().__init__(parent)
        self.panel, self.kind = panel, kind
        self.setFixedHeight(72)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(color("chart_background")))
        canvas = self.panel.canvas
        history = list(self.panel.service.derivatives.history.get((self.panel.instrument, self.kind), ()))
        p.setPen(QColor(color("text_secondary")))
        label = {"oi": "Open Interest", "funding": "Funding", "mark": "Mark",
                 "index": "Index", "basis": "Basis %"}[self.kind]
        stale = bool(history and time.time()-history[-1][0] > 120)
        p.drawText(8, 15, label + (" · 过期" if stale else ""))
        if not history or canvas.left_time is None:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "等待公开数据")
            return
        begin = canvas.left_time/1000
        end = begin+canvas.count*canvas.interval/1000
        points = [(stamp, float(value)) for stamp, value in history if begin <= stamp <= end]
        if not points:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "当前视口无数据")
            return
        left, right, top, bottom = 45., self.width()-75., 21., self.height()-8.
        low, high = min(v for _, v in points), max(v for _, v in points)
        if low == high:
            low -= 1
            high += 1
        path = QPainterPath()
        for index, (stamp, value) in enumerate(points):
            x = left+(stamp-begin)/(end-begin)*(right-left)
            y = bottom-(value-low)/(high-low)*(bottom-top)
            (path.moveTo if index == 0 else path.lineTo)(QPointF(x, y))
        p.setPen(QPen(QColor(color("focus")), 1.3))
        p.drawPath(path)
        p.setPen(QColor(color("chart_axis")))
        p.drawText(QRectF(right+3, top, 70, 18), Qt.AlignmentFlag.AlignLeft, f"{high:.5g}")
        p.drawText(QRectF(right+3, bottom-15, 70, 18), Qt.AlignmentFlag.AlignLeft, f"{low:.5g}")
