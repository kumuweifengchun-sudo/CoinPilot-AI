"""离线 SVG 图标；按控件状态着色并由 Qt 以实际 DPI 渲染。"""
from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from .visuals import resource_path

ALIASES = {
    "monitoring": "chart-candlestick", "trade": "arrow-down-up", "review": "notebook-pen",
    "settings": "settings-2", "cursor": "mouse-pointer-2", "horizontal": "minus",
    "ray": "move-up-right", "vertical": "move-vertical", "rectangle": "rectangle-horizontal",
    "text": "type", "measure": "ruler", "ema": "chart-no-axes-combined",
    "objects": "layers", "delete": "trash", "workbench": "panel-top", "mini": "monitor",
}
# 仅下列专用图表符号为本项目原创，其余保留 Lucide 许可。
CUSTOM = {
    "trend": '<path d="M5 18 19 6"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="6" r="2"/>',
    "fib": '<path d="M4 4h16M4 10h12M4 15h16M4 20h10M4 4v16"/>',
    "brand": '<path d="M5 6v12M12 3v18M19 8v10"/><rect x="3" y="9" width="4" height="5" rx="1"/><rect x="10" y="6" width="4" height="9" rx="1"/><rect x="17" y="11" width="4" height="4" rx="1"/>',
}


class SvgIconEngine(QIconEngine):
    def __init__(self, source, color=None):
        super().__init__()
        self.source, self.color = source, color
        self.renderers = {}

    def clone(self):
        return SvgIconEngine(self.source, self.color)

    def paint(self, painter, rect, mode, state):
        from .theme import color as theme_color
        explicit = theme_color(self.color[1:]) if self.color and self.color.startswith("@") else self.color
        color = theme_color("text_muted") if mode == QIcon.Mode.Disabled else (
            theme_color("text") if state == QIcon.State.On or mode == QIcon.Mode.Selected
            else explicit or theme_color("text_secondary"))
        if color not in self.renderers:
            data = self.source.replace("currentColor", color).replace("#94a3b8", color)
            self.renderers[color] = QSvgRenderer(QByteArray(data.encode()))
        self.renderers[color].render(painter, QRectF(rect))

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.)

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = QPixmap(round(size.width()*scale), round(size.height()*scale))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        return pixmap


@lru_cache(maxsize=160)
def icon(name, color=None):
    name = ALIASES.get(name, name)
    if name in CUSTOM:
        source = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' + CUSTOM[name] + '</svg>'
    else:
        source = resource_path(f"coinpilot_ai/assets/lucide/{name}.svg").read_text(encoding="utf-8")
    return QIcon(SvgIconEngine(source, color))


def set_button_icon(button, name, *, size=16, color=None):
    button.setIcon(icon(name, color))
    button.setIconSize(QSize(size, size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAccessibleName(button.toolTip() or button.text())
    return button
