"""由定义描述驱动的指标 Pane，与主图共享时间轴、缩放和十字光标。"""
import math
from bisect import bisect_left
from collections.abc import Sequence
from typing import TYPE_CHECKING, override

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import QWidget

from coinpilot_ai.indicators.validation import instance_from_record
from coinpilot_ai.indicators.models import PlotDefinition
from coinpilot_ai.indicators.runtime.result import IndicatorResult
from coinpilot_ai.ui.theme import color
from .plots import draw_plot

if TYPE_CHECKING:
    from .canvas import CandleChart


class IndicatorStrip(QWidget):
    def __init__(self, canvas: "CandleChart", indicator_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas, self.indicator_id = canvas, indicator_id
        self.setMinimumHeight(70)
        self.resize(self.width(), 112)
        self.setMouseTracking(True)
        self.crosshair_time: float | None = None
        self.pan_anchor: tuple[float, float] | None = None
        canvas.crosshair_changed.connect(self.show_crosshair)

    def show_crosshair(self, stamp: object) -> None:
        self.crosshair_time = float(stamp) if isinstance(stamp, (int, float)) else None
        self.update()

    def plot_bounds(self) -> QRectF:
        main, _ = self.canvas.plot_rects()
        offset = self.mapFromGlobal(self.canvas.mapToGlobal(QPoint(0, 0))).x()
        return QRectF(main.left() + offset, 22., main.width(), max(1., self.height() - 30.))

    def legend_values(self, result: IndicatorResult, plots: Sequence[PlotDefinition]) -> list[str]:
        """按主图同一根 K 线取值；缺失结果不回退到其他时刻。"""
        times = self.canvas.times
        stamp = times[-1] if times else None
        if times and self.crosshair_time is not None:
            # crosshair_changed 传递横轴坐标，K 线中心位于开盘时间后半个间隔。
            target = self.crosshair_time - self.canvas.interval / 2
            index = bisect_left(times, target)
            selected = min(range(max(0, index - 1), min(len(times), index + 1)),
                           key=lambda i: abs(times[i] - target))
            stamp = times[selected]
        index = bisect_left(result.times, stamp) if stamp is not None else len(result.times)
        matched = index < len(result.times) and result.times[index] == stamp
        labels: list[str] = []
        for plot in plots:
            values = result.outputs.get(plot.output, ())
            value = values[index] if matched and index < len(values) else None
            if value is None or not math.isfinite(value):
                labels.append("—")
            elif result.metadata.output_types.get(plot.output) == "bool":
                labels.append("真" if value else "假")
            else:
                labels.append(f"{value:,.6g}")
        return labels

    @override
    def wheelEvent(self, a0: QWheelEvent | None) -> None:
        if a0 is not None and self.canvas.times:
            bounds = self.plot_bounds()
            main, _ = self.canvas.plot_rects()
            x = main.left() + (a0.position().x() - bounds.left()) / bounds.width() * main.width()
            self.canvas.zoom_time(math.exp(-a0.angleDelta().y() / 120 * .16), x)
            a0.accept()

    @override
    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is not None and a0.button() == Qt.MouseButton.LeftButton and self.canvas.left_time is not None:
            self.pan_anchor = a0.position().x(), self.canvas.left_time
            a0.accept()

    @override
    def mouseMoveEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is None or self.canvas.left_time is None:
            return
        bounds = self.plot_bounds()
        span = self.canvas.count * self.canvas.interval
        if self.pan_anchor is not None:
            x, stamp = self.pan_anchor
            self.canvas.left_time = stamp - (a0.position().x() - x) / bounds.width() * span
            self.canvas.follow = False
            self.canvas.change_view()
        stamp = self.canvas.left_time + (a0.position().x() - bounds.left()) / bounds.width() * span
        self.canvas.external_crosshair_time = stamp
        self.canvas.crosshair_changed.emit(stamp)
        self.canvas.update()

    @override
    def mouseReleaseEvent(self, a0: QMouseEvent | None) -> None:
        self.pan_anchor = None
        if a0 is not None:
            a0.accept()

    @override
    def leaveEvent(self, a0: QEvent | None) -> None:
        self.canvas.external_crosshair_time = None
        self.canvas.crosshair_changed.emit(None)
        self.canvas.update()
        super().leaveEvent(a0)

    @override
    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(color("chart_background")))
        item = next(((setting, result) for setting, result in self.canvas.indicator_results
                     if setting["id"] == self.indicator_id), None)
        if item is None or not self.canvas.times or self.canvas.book is None:
            return
        setting, result = item
        instance = instance_from_record(setting, self.canvas.book.catalog)
        definition = self.canvas.book.catalog.resolve(instance.definition, instance.definition_revision)
        plots = [plot for plot in definition.plots if plot.target == "pane" and instance.plots[plot.id].visible]
        title = definition.name + " · " + ", ".join(str(value) for value in instance.inputs.values())
        painter.save()
        painter.setClipRect(QRectF(8, 0, max(0, self.width() - 16), 22))
        painter.setPen(QColor(color("text_secondary")))
        painter.drawText(8, 15, title)
        xlegend = 8 + painter.fontMetrics().horizontalAdvance(title) + 16
        value_plots = [plot for plot in plots if plot.type != "horizontal_line"]
        for plot, value in zip(value_plots, self.legend_values(result, value_plots)):
            text = f"{plot.title}  {value}" if len(value_plots) > 1 else value
            painter.setPen(QColor(instance.plots[plot.id].color))
            painter.drawText(QPointF(xlegend, 15), text)
            xlegend += painter.fontMetrics().horizontalAdvance(text) + 16
        painter.restore()
        bounds = self.plot_bounds()
        start, end = self.canvas.visible()
        numbers = [value for plot in plots if plot.type in ("line", "histogram")
                   for value in result.outputs.get(plot.output, ())[start:end] if value is not None]
        numbers.extend(definition.pane.scale.levels)
        numbers.extend(plot.value for plot in plots if plot.value is not None)
        if any(plot.type == "histogram" for plot in plots):
            numbers.append(0.)
        if not numbers and any(plot.type in ("shape", "background") for plot in plots):
            numbers = [0., 1.]
        if not numbers:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "历史预热不足")
            return
        minimum, maximum = min(numbers), max(numbers)
        scale = definition.pane.scale
        if scale.minimum is not None:
            minimum = scale.minimum
        if scale.maximum is not None:
            maximum = scale.maximum
        if maximum == minimum:
            maximum, minimum = maximum + 1, minimum - 1
        def x_at(index: int) -> float:
            return bounds.left() + (self.canvas.times[index] - self.canvas.left_time + self.canvas.render_interval / 2) / self.canvas.interval / self.canvas.count * bounds.width()
        def y_at(value: float) -> float:
            return bounds.bottom() - (value - minimum) / (maximum - minimum) * bounds.height()
        painter.save()
        painter.setClipRect(bounds)
        painter.setPen(QPen(QColor(color("chart_grid")), .7, Qt.PenStyle.DashLine))
        for level in scale.levels:
            painter.drawLine(QPointF(bounds.left(), y_at(level)), QPointF(bounds.right(), y_at(level)))
        painter.restore()
        for plot in plots:
            draw_plot(painter, plot, result.outputs.get(plot.output, ()), self.canvas.times,
                      start=start, end=end, x_at=x_at, y_at=y_at, bounds=bounds, style=instance.plots[plot.id],
                      bar=self.canvas.render_bar, bar_width=max(1., bounds.width() / self.canvas.count * .64))
        if self.crosshair_time is not None and self.canvas.left_time is not None:
            x = bounds.left() + (self.crosshair_time - self.canvas.left_time) / (self.canvas.count * self.canvas.interval) * bounds.width()
            if bounds.left() <= x <= bounds.right():
                painter.setPen(QPen(QColor(color("chart_axis")), .7, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
        painter.setPen(QColor(color("chart_axis")))
        painter.drawText(QRectF(bounds.right() + 4, bounds.top(), 80, 16), Qt.AlignmentFlag.AlignLeft, f"{maximum:.5g}")
        painter.drawText(QRectF(bounds.right() + 4, bounds.bottom() - 16, 80, 16), Qt.AlignmentFlag.AlignLeft, f"{minimum:.5g}")
