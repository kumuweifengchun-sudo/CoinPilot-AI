"""本地模拟账户资产曲线。"""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from coinpilot_ai.ui.theme import color


class EquityCurve(QWidget):
    def __init__(self, store, scope, parent=None):
        super().__init__(parent)
        self.store, self.scope = store, scope
        self.points = None
        self.setMinimumHeight(95)

    def set_points(self, points):
        self.points = list(points) if points is not None else None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(color("chart_background")))
        p.setPen(QColor(color("text_secondary")))
        p.drawText(8, 17, "资产曲线 · USDT")
        rows = (self.points if self.points is not None else
                sorted((row for _, row in self.store.list("paper_equity", self.scope)),
                       key=lambda row: int(row["time"])))
        values = [float(row["equity"]) for row in rows]
        if not values:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无模拟成交")
            return
        left, right, top, bottom = 45, self.width()-65, 27, self.height()-16
        if right <= left or bottom <= top:
            return
        minimum, maximum = min(values), max(values)
        if minimum == maximum:
            minimum -= 1
            maximum += 1
        path = QPainterPath()
        for index, value in enumerate(values):
            x = left+(right-left)*index/max(1, len(values)-1)
            y = bottom-(value-minimum)/(maximum-minimum)*(bottom-top)
            (path.moveTo if index == 0 else path.lineTo)(QPointF(x, y))
        p.setPen(QPen(QColor(color("positive" if values[-1] >= values[0] else "negative")), 1.7))
        p.drawPath(path)
        p.setPen(QColor(color("chart_axis")))
        p.drawText(QRectF(right+3, top, 60, 20), Qt.AlignmentFlag.AlignLeft, f"{maximum:.6g}")
        p.drawText(QRectF(right+3, bottom-15, 60, 20), Qt.AlignmentFlag.AlignLeft, f"{minimum:.6g}")
