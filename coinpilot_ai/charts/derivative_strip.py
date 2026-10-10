"""显示公开衍生品快照及本次运行积累的采样，时间轴与主图对齐。"""
import time
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, override

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPaintEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from coinpilot_ai.ui.theme import color

if TYPE_CHECKING:
    from .panel import ChartPanel


class DerivativeStrip(QWidget):
    def __init__(self, panel: "ChartPanel", kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.panel, self.kind = panel, kind
        self.setFixedHeight(88)

    def format_value(self, value: Decimal) -> str:
        if self.kind == "funding":
            return f"{value * 100:,.6g}%"
        if self.kind == "basis":
            return f"{value:,.6g}%"
        return f"{value:,.6g}"

    def summary(self) -> str:
        label = {"oi": "Open Interest", "funding": "Funding", "mark": "Mark",
                 "index": "Index", "basis": "Basis %"}[self.kind]
        history = self.panel.service.derivatives.history.get((self.panel.instrument, self.kind))
        if not history:
            return label + "  —"
        stamp, value = history[-1]
        state = "过期" if time.time() - stamp > 120 else "最新"
        return f"{label}  {state} {self.format_value(value)}  · {datetime.fromtimestamp(stamp):%H:%M:%S}"

    def plot_bounds(self) -> QRectF:
        canvas = self.panel.canvas
        main, _ = canvas.plot_rects()
        offset = self.mapFromGlobal(canvas.mapToGlobal(QPoint(0, 0))).x()
        return QRectF(main.left() + offset, 37., main.width(), max(1., self.height() - 46.))

    @override
    def paintEvent(self, a0: QPaintEvent | None) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(color("chart_background")))
        canvas = self.panel.canvas
        monitor = self.panel.service.derivatives
        history = list(monitor.history.get((self.panel.instrument, self.kind), ()))
        p.setPen(QColor(color("text_secondary")))
        p.drawText(8, 15, self.summary())
        error = monitor.errors.get((self.panel.instrument, self.kind))
        if self.kind == "basis":
            error = monitor.errors.get((self.panel.instrument, "mark")) or monitor.errors.get((self.panel.instrument, "index"))
        p.drawText(8, 30, error or "仅本次运行采样 · 每分钟刷新")
        if not history or canvas.left_time is None:
            p.drawText(QRectF(8, 37, self.width() - 16, self.height() - 37), Qt.AlignmentFlag.AlignCenter,
                       error or "等待公开数据")
            return
        begin = canvas.left_time / 1000
        end = begin + canvas.count * canvas.interval / 1000
        points = [(stamp, float(value * 100 if self.kind == "funding" else value))
                  for stamp, value in history if begin <= stamp <= end]
        bounds = self.plot_bounds()
        if not points:
            p.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "当前视口无采样 · 未加载启动前的历史数据")
            return
        low, high = min(v for _, v in points), max(v for _, v in points)
        if low == high:
            padding = max(abs(low) * .0001, 1e-8)
            low, high = low - padding, high + padding
        path = QPainterPath()
        previous: float | None = None
        last = QPointF()
        for stamp, value in points:
            x = bounds.left() + (stamp - begin) / (end - begin) * bounds.width()
            y = bounds.bottom() - (value - low) / (high - low) * bounds.height()
            last = QPointF(x, y)
            # 请求失败或休眠后的缺口不连成连续行情。
            if previous is None or stamp - previous > 120:
                path.moveTo(last)
            else:
                path.lineTo(last)
            previous = stamp
        p.save()
        p.setClipRect(bounds.adjusted(-3, -3, 3, 3))
        shade = QColor(color("focus"))
        p.setPen(QPen(shade, 1.3))
        p.drawPath(path)
        # 单点与很短的采样窗口也必须可见，不能只画 moveTo。
        p.setBrush(shade)
        p.drawEllipse(last, 3., 3.)
        p.restore()
        p.setPen(QColor(color("chart_axis")))
        suffix = "%" if self.kind in ("funding", "basis") else ""
        p.drawText(QRectF(bounds.right() + 3, bounds.top(), 80, 18), Qt.AlignmentFlag.AlignLeft, f"{high:.5g}{suffix}")
        p.drawText(QRectF(bounds.right() + 3, bounds.bottom() - 15, 80, 18), Qt.AlignmentFlag.AlignLeft, f"{low:.5g}{suffix}")
