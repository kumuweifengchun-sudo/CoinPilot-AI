"""主题兼容与自绘颜色切换。"""
from decimal import Decimal

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QImage, QPainter, QPalette

from coinpilot_ai.core.config import DEFAULT_CONFIG, SettingsStore, validate_config
from coinpilot_ai.ui.theme import (DEFAULT_THEME_ID, Theme, activate_theme, color, get_theme,
                                 register_theme, style_sheet)
from coinpilot_ai.desktop.visuals import Quote, draw_ticker
from coinpilot_ai.charts.canvas import CandleChart


def test_theme_config_preserves_legacy_and_rejects_unknown(tmp_path):
    store = SettingsStore(tmp_path / "legacy.json")
    store.path.write_text('{"symbol1":"ETHUSDT"}', encoding="utf-8")
    assert store.load()["ui_theme"] == DEFAULT_THEME_ID
    assert not store.warning
    result, corrected = validate_config({**DEFAULT_CONFIG, "ui_theme": "unknown"})
    assert result["ui_theme"] == DEFAULT_THEME_ID
    assert corrected == ["ui_theme"]
    store.save({**DEFAULT_CONFIG, "ui_theme": DEFAULT_THEME_ID})
    assert store.load()["ui_theme"] == DEFAULT_THEME_ID


def test_registered_theme_recolors_palette_qss_and_mini_paint(app):
    alternative = Theme("test_dark_alt", get_theme(DEFAULT_THEME_ID).colors | {
        "background": "#202122", "mini_background": "#303132",
        "chart_background": "#303132", "accent": "#D7D8D9"})
    register_theme(alternative)

    def render():
        image = QImage(100, 28, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        draw_ticker(painter, QRectF(0, 0, 100, 28), Quote("BTCUSDT", Decimal("1")), 2, 12, 1, mini=True)
        painter.end()
        return image.pixelColor(50, 3).name()

    chart = CandleChart()
    chart.resize(280, 200)
    chart.show()
    app.processEvents()
    try:
        activate_theme(DEFAULT_THEME_ID)
        original = render()
        original_chart = chart.grab().toImage().pixelColor(5, 5).name()
        assert activate_theme(alternative.id) == alternative.id
        assert color("accent") == "#D7D8D9"
        assert alternative.colors["background"] in style_sheet()
        assert app.palette().color(QPalette.ColorRole.Window).name().upper() == "#202122"
        assert render() != original
        app.processEvents()
        assert chart.grab().toImage().pixelColor(5, 5).name() != original_chart
    finally:
        activate_theme(DEFAULT_THEME_ID)
        chart.close()
