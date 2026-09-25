"""多尺寸应用标志与按控件状态着色的离线 SVG 功能图标。"""
from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from coinpilot_ai.core.paths import resource_path

ALIASES = {
    "monitoring": "chart-candlestick", "trade": "arrow-down-up", "review": "notebook-pen",
    "settings": "settings-2", "cursor": "mouse-pointer-2", "horizontal": "minus",
    "ray": "move-up-right", "vertical": "move-vertical", "rectangle": "rectangle-horizontal",
    "text": "type", "measure": "ruler", "ema": "chart-no-axes-combined",
    "objects": "layers", "delete": "trash", "workbench": "panel-top", "mini": "monitor",
}
# 下列图表及窗口控制符号为本项目原创，其余保留 Lucide 许可。
CUSTOM = {
    "trend": '<path d="M5 18 19 6"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="6" r="2"/>',
    "fib": '<path d="M4 4h16M4 10h12M4 15h16M4 20h10M4 4v16"/>',
    "window-minimize": '<path d="M5 12h14"/>',
    "window-maximize": '<rect x="5" y="5" width="14" height="14" rx=".5"/>',
    "window-restore": '<path d="M9 6V4h11v11h-2"/><rect x="4" y="9" width="11" height="11" rx=".5"/>',
    "window-close": '<path d="m6 6 12 12M18 6 6 18"/>',
}

APP_ICON_PATH = "coinpilot_ai/assets/app_icon/app.ico"
APP_ICON_SIZES = (16, 32, 64, 128, 256)


@lru_cache(maxsize=1)
def application_icon():
    return QIcon(str(resource_path(APP_ICON_PATH)))


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
    if name == "brand":
        return application_icon()
    name = ALIASES.get(name, name)
    if name in CUSTOM:
        source = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' + CUSTOM[name] + '</svg>'
    else:
        source = resource_path(f"coinpilot_ai/assets/lucide/{name}.svg").read_text(encoding="utf-8")
    return QIcon(SvgIconEngine(source, color))


def brand_pixmap(size=64, *, badge=None):
    """保留原始应用标志颜色；badge 为可选的托盘状态颜色令牌。"""
    from .theme import color as theme_color

    # 构造真实像素帧；不能让当前屏幕 DPR 改写托盘可用尺寸。
    pixmap = application_icon().pixmap(QSize(size, size), 1.0)
    if not badge:
        return pixmap
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 64, size / 64)
    painter.setBrush(QColor(theme_color(badge)))
    painter.setPen(QColor(theme_color("surface_raised")))
    painter.drawEllipse(45, 3, 16, 16)
    painter.end()
    return pixmap


def tray_icon(badge=None):
    if not badge:
        return application_icon()
    result = QIcon()
    for size in APP_ICON_SIZES:
        result.addPixmap(brand_pixmap(size, badge=badge))
    return result


def set_button_icon(button, name, *, size=16, color=None):
    button.setIcon(icon(name, color))
    button.setIconSize(QSize(size, size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAccessibleName(button.toolTip() or button.text())
    return button
