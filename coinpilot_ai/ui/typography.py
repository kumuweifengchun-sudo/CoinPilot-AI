"""应用共用字体加载、字形配置与绘制质量设置。"""
from PyQt6.QtGui import QFont, QFontDatabase, QPainter

from coinpilot_ai.core.paths import resource_path

FONT_FAMILY = "Inter Variable"
CHINESE_FONT_FAMILY = "Microsoft YaHei UI"


def load_fonts():
    """从应用资源加载 Inter 4.1；中文由系统字体补齐。"""
    families = []
    for name in ("InterVariable.ttf", "InterVariable-Italic.ttf"):
        font_id = QFontDatabase.addApplicationFont(
            str(resource_path(f"coinpilot_ai/assets/fonts/{name}")))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return FONT_FAMILY in families


def font(size, bold=False, latin=False):
    result = QFont()
    result.setFamilies([FONT_FAMILY, CHINESE_FONT_FAMILY])
    result.setPixelSize(size)
    result.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    result.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    result.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    if latin:
        result.setFeature(QFont.Tag("tnum"), 1)
    return result


def render_hints(painter):
    painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                           | QPainter.RenderHint.SmoothPixmapTransform)
