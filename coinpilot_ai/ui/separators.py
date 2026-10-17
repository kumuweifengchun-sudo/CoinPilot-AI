"""应用级布局度量与分隔条绘制；交互和尺寸保存仍交给 Qt 原生控件。"""
from typing import override

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QDialog, QProxyStyle, QStyle, QStyleOption, QWidget

from .theme import SPACING_MD, SPACING_SM, SPACING_XS, color


class PanelStyle(QProxyStyle):
    """为现有及新建页面提供统一留白，并绘制停靠区与 QSplitter 把手。"""

    @override
    def pixelMetric(self, metric: QStyle.PixelMetric, option: QStyleOption | None = None,
                    widget: QWidget | None = None) -> int:
        if metric in (QStyle.PixelMetric.PM_LayoutHorizontalSpacing,
                      QStyle.PixelMetric.PM_LayoutVerticalSpacing):
            return SPACING_SM
        if metric in (QStyle.PixelMetric.PM_LayoutLeftMargin, QStyle.PixelMetric.PM_LayoutTopMargin,
                      QStyle.PixelMetric.PM_LayoutRightMargin, QStyle.PixelMetric.PM_LayoutBottomMargin):
            # 嵌入设置页的 QDialog 使用 Widget 标志，按普通页面处理。
            return SPACING_MD if isinstance(widget, QDialog) and widget.isWindow() else SPACING_SM
        if metric in (QStyle.PixelMetric.PM_DockWidgetSeparatorExtent,
                      QStyle.PixelMetric.PM_SplitterWidth):
            return SPACING_XS
        return super().pixelMetric(metric, option, widget)

    @override
    def drawPrimitive(self, element: QStyle.PrimitiveElement, option: QStyleOption | None,
                      painter: QPainter | None, widget: QWidget | None = None) -> None:
        if element == QStyle.PrimitiveElement.PE_IndicatorDockWidgetResizeHandle:
            self._draw_separator(option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)

    @override
    def drawControl(self, element: QStyle.ControlElement, option: QStyleOption | None,
                    painter: QPainter | None, widget: QWidget | None = None) -> None:
        if element == QStyle.ControlElement.CE_Splitter:
            self._draw_separator(option, painter)
            return
        super().drawControl(element, option, painter, widget)

    @staticmethod
    def _draw_separator(option: QStyleOption | None, painter: QPainter | None) -> None:
        if option is None or painter is None:
            return
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        active = enabled and bool(option.state & (QStyle.StateFlag.State_MouseOver |
                                                  QStyle.StateFlag.State_Sunken))
        rect = option.rect
        painter.save()
        painter.fillRect(rect, QColor(color("surface_hover" if active else "background")))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("accent" if active else "border_strong" if enabled else "border")))
        # 以逻辑像素绘制，让 Qt 处理高 DPI；点列沿分隔缝的长边排列。
        center = QPointF(rect.x() + rect.width() / 2, rect.y() + rect.height() / 2)
        horizontal = rect.width() > rect.height()
        if max(rect.width(), rect.height()) >= SPACING_XS * 3:
            for offset in (-SPACING_XS, 0, SPACING_XS):
                point = center + (QPointF(offset, 0) if horizontal else QPointF(0, offset))
                painter.drawEllipse(point, 1.0, 1.0)
        painter.restore()
