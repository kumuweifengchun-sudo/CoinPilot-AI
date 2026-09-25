"""悬浮窗与设置预览共用的绘制逻辑。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPixmap

from coinpilot_ai.market.providers import split_symbol

from coinpilot_ai.ui.typography import font, render_hints


def price_text_width(text, metrics):
    # 系统字体的 tnum 特性可能不受渲染后端支持，显式数字格保证各位宽度一致。
    if text and all(char in "0123456789,." for char in text):
        digit_width = max(metrics.horizontalAdvance(str(digit)) for digit in range(10))
        return sum(digit_width if char.isdigit() else metrics.horizontalAdvance(char) for char in text)
    return metrics.horizontalAdvance(text)


def snap_point(painter, x, y):
    transform = painter.deviceTransform()
    inverse, invertible = transform.inverted()
    if not invertible:
        return QPointF(x, y)
    physical = transform.map(QPointF(x, y))
    return inverse.map(QPointF(round(physical.x()), round(physical.y())))


def draw_price(painter, rect, text, *, align_left=False):
    metrics = QFontMetricsF(painter.font(), painter.device())
    baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
    x = rect.left() if align_left else rect.right() - price_text_width(text, metrics)
    if not text or not all(char in "0123456789,." for char in text):
        painter.drawText(snap_point(painter, x, baseline), text)
        return
    digit_width = max(metrics.horizontalAdvance(str(digit)) for digit in range(10))
    for char in text:
        actual_width = metrics.horizontalAdvance(char)
        cell_width = digit_width if char.isdigit() else actual_width
        painter.drawText(snap_point(painter, x + (cell_width - actual_width) / 2, baseline), char)
        x += cell_width


@dataclass
class Quote:
    symbol: str
    price: Decimal | None = None
    error: str = ""
    updated_at: datetime | None = None
    icon: QPixmap | None = None
    source: str = ""

    def accept(self, price, error, source=""):
        self.error = error
        if price is not None:
            self.price = price
            self.updated_at = datetime.now()
            self.source = source

    def price_text(self, decimals):
        if self.price is not None:
            return f"{self.price:,.{decimals}f}"
        return "暂无数据" if self.error else "加载中"

    def status_text(self):
        if self.error:
            return "更新失败 · 保留上次价格" if self.price is not None else "更新失败 · 等待重试"
        return f"更新于 {self.updated_at:%H:%M:%S}" if self.updated_at else "正在获取行情"


def fallback_icon(symbol, size=96):
    from coinpilot_ai.ui.theme import color
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    render_hints(painter)
    colors = [color("warning"), color("focus"), color("positive"), color("text_secondary"), color("negative")]
    painter.setBrush(QColor(colors[sum(map(ord, symbol)) % len(colors)]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(0, 0, size, size))
    painter.setFont(font(size // 2, True))
    painter.setPen(QColor(color("accent_text")))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, symbol[:1])
    painter.end()
    return result


def ticker_size(quotes, decimals, size, mini=False, device=None):
    if mini:
        metrics = QFontMetricsF(font(12, True, latin=True), device)
        width = 80.0
        for quote, precision in zip(quotes, decimals):
            price_width = max(price_text_width(quote.price_text(precision), metrics),
                              price_text_width(f"88,888.{('8' * precision)}" if precision else "88,888", metrics))
            width = max(width, 8 + 16 + 4 + price_width + 18)
        return math.ceil(width + 1), 28
    main = QFontMetricsF(font(size, True, latin=True), device)
    small = QFontMetricsF(font(11), device)
    width = 280.0
    icon = max(30, size * 1.5)
    for quote, precision in zip(quotes, decimals):
        base, counter = split_symbol(quote.symbol)
        left = max(main.horizontalAdvance(base), small.horizontalAdvance(f"永续 · {counter}"))
        right = max(
            price_text_width(quote.price_text(precision), main),
            price_text_width(f"88,888.{('8' * precision)}" if precision else "88,888", main),
            small.horizontalAdvance("更新失败 · 保留上次价格"),
        )
        width = max(width, 18 + icon + 12 + left + 28 + right + 18)
    return math.ceil(width + 1), max(76, int(size * 1.5 + 34))


def draw_ticker(painter, rect, quote, precision, size, opacity, sample=False, mini=False):
    from coinpilot_ai.ui.theme import color
    painter.save()
    render_hints(painter)
    border = QColor(color("mini_border"))
    border.setAlpha(max(35, int(opacity * 65)))
    painter.setPen(border)
    # Windows 将分层窗口中 alpha 为 0 的像素视为可穿透，空白处便无法触发悬停提示。
    background = QColor(color("mini_background"))
    background.setAlpha(max(1, round(opacity * 255)))
    painter.setBrush(background)
    radius = 7 if mini else 16
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    if mini:
        base, _ = split_symbol(quote.symbol)
        if quote.icon is None:
            quote.icon = fallback_icon(base)
        icon_rect = QRectF(rect.x() + 8, rect.center().y() - 8, 16, 16)
        painter.drawPixmap(icon_rect, quote.icon, QRectF(quote.icon.rect()))
        painter.setFont(font(12, True, latin=True))
        text_rect = QRectF(icon_rect.right() + 4, rect.y(), rect.width() - 46, rect.height())
        painter.setPen(QColor(color("warning" if quote.error else "mini_text")))
        draw_price(painter, text_rect, quote.price_text(precision), align_left=True)
        if quote.error:
            painter.drawText(QRectF(rect.right() - 15, rect.y(), 9, rect.height()),
                             Qt.AlignmentFlag.AlignCenter, "!")
        painter.restore()
        return
    icon_size = max(30, size * 1.5)
    icon_rect = QRectF(rect.x() + 18, rect.center().y() - icon_size / 2, icon_size, icon_size)
    if quote.icon is None:
        quote.icon = fallback_icon(split_symbol(quote.symbol)[0])
    painter.drawPixmap(icon_rect, quote.icon, QRectF(quote.icon.rect()))
    base, counter = split_symbol(quote.symbol)
    left = icon_rect.right() + 12
    top = rect.center().y() - (size + 21) / 2
    painter.setFont(font(size, True, latin=True))
    painter.setPen(QColor(color("mini_text")))
    painter.drawText(QRectF(left, top, rect.width() - 36, size + 4), Qt.AlignmentFlag.AlignLeft, base)
    draw_price(painter, QRectF(left, top, rect.right() - left - 18, size + 4), quote.price_text(precision))
    painter.setFont(font(11))
    painter.setPen(QColor(color("text_muted")))
    painter.drawText(QRectF(left, top + size + 8, 220, 18), Qt.AlignmentFlag.AlignLeft,
                     f"永续 · {counter}" if counter else "永续合约行情")
    painter.setPen(QColor(color("warning" if quote.error else "text_secondary")))
    painter.drawText(QRectF(left, top + size + 8, rect.right() - left - 18, 18),
                     Qt.AlignmentFlag.AlignRight, "示例行情 · 仅供预览" if sample else quote.status_text())
    painter.restore()
