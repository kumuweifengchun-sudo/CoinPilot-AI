"""按绘图描述绘制输出；不识别指标名称。"""
from collections.abc import Callable, Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from coinpilot_ai.indicators.models import PlotDefinition, PlotStyle
from coinpilot_ai.market.intervals import contiguous


def draw_plot(painter: QPainter, plot: PlotDefinition, values: Sequence[float | None], times: Sequence[int], *,
              start: int, end: int, x_at: Callable[[int], float], y_at: Callable[[float], float],
              bounds: QRectF, style: PlotStyle, bar: str, bar_width: float) -> None:
    if not style.visible:
        return
    painter.save()
    painter.setClipRect(bounds, Qt.ClipOperation.IntersectClip)
    shade = QColor(style.color)
    painter.setPen(QPen(shade, style.width))
    if plot.type == "horizontal_line":
        value = plot.value
        if value is None:
            value = next((item for item in reversed(values) if item is not None), None)
        if value is not None:
            painter.drawLine(QPointF(bounds.left(), y_at(value)), QPointF(bounds.right(), y_at(value)))
    elif plot.type == "line":
        path = QPainterPath()
        active = False
        for index in range(start, min(end, len(values), len(times))):
            value = values[index]
            if value is None:
                active = False
                continue
            point = QPointF(x_at(index), y_at(value))
            if not active or index == 0 or not contiguous(times[index - 1], times[index], bar):
                path.moveTo(point)
            else:
                path.lineTo(point)
            active = True
        painter.drawPath(path)
    else:
        painter.setBrush(shade)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(start, min(end, len(values), len(times))):
            value = values[index]
            if value is None:
                continue
            x = x_at(index)
            if plot.type == "histogram":
                zero, y = y_at(0), y_at(value)
                painter.drawRect(QRectF(x - bar_width / 2, min(zero, y), max(1, bar_width), max(1, abs(y - zero))))
            elif plot.type == "shape" and bool(value):
                y = bounds.top() + 8 if plot.placement == "top" else bounds.bottom() - 8
                if plot.marker == "circle":
                    painter.drawEllipse(QPointF(x, y), 4, 4)
                elif plot.marker == "cross":
                    painter.setPen(QPen(shade, style.width))
                    painter.drawLine(QPointF(x - 4, y - 4), QPointF(x + 4, y + 4))
                    painter.drawLine(QPointF(x - 4, y + 4), QPointF(x + 4, y - 4))
                    painter.setPen(Qt.PenStyle.NoPen)
                else:
                    tip = y - 7 if plot.marker == "triangle_up" else y + 7
                    painter.drawPolygon(QPolygonF([QPointF(x, tip), QPointF(x - 5, y), QPointF(x + 5, y)]))
            elif plot.type == "background" and bool(value):
                shade.setAlpha(35)
                painter.fillRect(QRectF(x - bar_width / 2, bounds.top(), max(1, bar_width), bounds.height()), shade)
    painter.restore()
