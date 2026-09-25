"""自选列表以固定行高展示合约和右对齐价格，保持选中行为。"""
from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle

from coinpilot_ai.ui.typography import font
from coinpilot_ai.ui.theme import color


class WatchlistDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, 58)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("surface_selected" if selected else "surface_hover" if hover else "surface")))
        painter.drawRoundedRect(rect, 5, 5)
        if selected:
            painter.setBrush(QColor(color("accent")))
            painter.drawRoundedRect(QRect(rect.left(), rect.top()+10, 3, rect.height()-20), 1, 1)
        symbol = str(index.data(Qt.ItemDataRole.UserRole)).removesuffix("-USDT-SWAP")
        price = str(index.data(Qt.ItemDataRole.UserRole+1))
        fresh = index.data(Qt.ItemDataRole.UserRole+2)
        try:
            price = f"{float(price):,.2f}" if float(price) >= 10 else f"{float(price):,.6f}".rstrip("0").rstrip(".")
        except ValueError:
            pass
        painter.setFont(font(12, True, latin=True))
        painter.setPen(QColor(color("text")))
        title = QRect(rect.left()+12, rect.top()+7, rect.width()-24, 20)
        # 单行长币种/价格采用省略号，避免在窄侧栏重叠。
        price_width = min(round(title.width()*.60), painter.fontMetrics().horizontalAdvance(price)+2)
        symbol_rect = title.adjusted(0, 0, -price_width-8, 0)
        painter.drawText(symbol_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(symbol, Qt.TextElideMode.ElideRight, symbol_rect.width()))
        painter.setPen(QColor(color("text" if fresh else "text_muted")))
        price_rect = QRect(title.right()-price_width, title.top(), price_width+1, title.height())
        painter.drawText(price_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(price, Qt.TextElideMode.ElideRight, price_width))
        painter.setFont(font(10))
        painter.setPen(QColor(color("text_muted")))
        painter.drawText(QRect(rect.left()+12, rect.top()+30, rect.width()-24, 17),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "USDT 永续")
        painter.setPen(QColor(color("positive" if fresh else "text_muted")))
        painter.drawText(QRect(rect.left()+12, rect.top()+30, rect.width()-24, 17),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "OKX" if fresh else "待更新")
        painter.restore()
