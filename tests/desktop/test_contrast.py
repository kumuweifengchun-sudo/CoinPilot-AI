"""验证透明报价在深浅背景、缩放和生命周期中的可读性。"""

from decimal import Decimal

import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from coinpilot_ai.desktop.visuals import Quote, contrast_text_color, draw_ticker
from test_ui import widget  # noqa: F401


@pytest.mark.parametrize("background, expected", [
    ("#FFFFFF", "#000000"), ("#EEEEEE", "#000000"),
    ("#000000", "#ffffff"), ("#121519", "#ffffff"),
    ("#00FF00", "#000000"), ("#0000FF", "#ffffff"),
])
def test_text_contrast_uses_perceived_brightness(background, expected):
    assert contrast_text_color(QColor(background)).name() == expected


@pytest.mark.parametrize("scale", [1, 1.5, 2])
@pytest.mark.parametrize("mini", [True, False])
def test_price_recolors_each_digit_over_split_background(app, scale, mini):
    width, height = (130, 28) if mini else (340, 80)
    split = 55 if mini else 260
    band = QImage(width, 1, QImage.Format.Format_RGB32)
    for x in range(width):
        band.setPixelColor(x, 0, QColor("#121519" if x < split else "#FFFFFF"))
    image = QImage(round(width * scale), round(height * scale), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    draw_ticker(painter, QRectF(0, 0, width, height), Quote("BTCUSDT", Decimal("88888.88")),
                2, 24, 0, mini=mini, price_background=band)
    painter.end()
    # 检查字形不透明的内核，排除描边和抗锯齿边缘。
    start = 29 if mini else 220
    end = 88 if mini else width - 18
    for left, right, expected in ((start, split - 2, "#ffffff"), (split + 5, end, "#000000")):
        count = sum(image.pixelColor(x, y).alpha() > 250
                    and image.pixelColor(x, y).name() == expected
                    for x in range(round(left * scale), round(right * scale))
                    for y in range(round(8 * scale), round((20 if mini else 40) * scale)))
        assert count > 5


def test_background_refresh_hide_restore_and_shutdown(widget, monkeypatch):
    band = QImage(widget.logical_width, 1, QImage.Format.Format_RGB32)
    band.fill(QColor("white"))
    monkeypatch.setattr(widget, "_capture_price_background", lambda: band)
    widget.show()
    assert widget.contrast_timer.isActive()
    widget._refresh_price_contrast()
    assert widget._price_background == band
    widget.hide()
    assert not widget.contrast_timer.isActive()
    assert widget._price_background is None
    widget.show()
    widget._refresh_price_contrast()
    assert widget._price_background == band
    monkeypatch.setattr(widget, "_capture_price_background", lambda: None)
    widget._refresh_price_contrast()
    assert widget._price_background is None
    widget.shutdown()
    assert not widget.contrast_timer.isActive()
