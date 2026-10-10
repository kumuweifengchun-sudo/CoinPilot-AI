"""多空工具的半透明背景、线内数值和行情刷新回归。"""
from collections.abc import Iterator
from math import ceil

import pytest
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from coinpilot_ai.charts.canvas import CandleChart
from coinpilot_ai.charts.state import Drawing, drawing
from coinpilot_ai.ui.theme import color


@pytest.fixture
def position_chart(app: QApplication) -> Iterator[CandleChart]:
    chart = CandleChart()
    chart.resize(900, 550)
    chart.set_data([[str(1700000000000+i*900000), "100", "102", "98", "101", "10", "0", "0", "1"]
                    for i in range(100)])
    chart.auto_scale = False
    chart.low, chart.high = 80., 120.
    chart.price_stale = False
    try:
        yield chart
    finally:
        chart.close()
        chart.deleteLater()
        app.processEvents()


def position_object(chart: CandleChart, tool: str) -> Drawing:
    target, stop = (110., 95.) if tool == 'long_position' else (90., 105.)
    obj = drawing(tool, [chart.anchor(QPointF(100, chart.price_y(100))),
                         chart.anchor(QPointF(700, chart.price_y(target))),
                         chart.anchor(QPointF(700, chart.price_y(stop)))])
    obj['notional_usdt'] = '2500'
    return obj


def object_image(chart: CandleChart, obj: Drawing) -> QImage:
    image = QImage(chart.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setFont(chart.font())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    chart.draw_objects(painter, [obj])
    painter.end()
    return image


def assert_line_text(chart: CandleChart, obj: Drawing, image: QImage,
                     price: float, text: str, token: str) -> None:
    expected = QImage(image.size(), image.format())
    expected.fill(Qt.GlobalColor.transparent)
    painter = QPainter(expected)
    painter.setFont(chart.font())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    entry_y = chart.price_y(100.)
    for anchor, background_token in zip(obj['anchors'][1:], ('positive', 'negative')):
        boundary_y = chart.price_y(anchor[1])
        shade = QColor(color(background_token))
        shade.setAlpha(32)
        painter.fillRect(QRectF(100, min(entry_y, boundary_y), 600, abs(entry_y-boundary_y)), shade)
    painter.setPen(QColor(color(token)))
    # 与完整线段同宽的独立参考，验证文字确实位于线上中央。
    height = painter.fontMetrics().height()
    width = painter.fontMetrics().horizontalAdvance(text)
    y = chart.price_y(price)
    painter.drawText(QRectF(100, y-height/2, 600, height), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    crop = QRectF(400-width/2-1, y-height/2-1, width+2, height+2).toAlignedRect()
    assert image.copy(crop) == expected.copy(crop)


@pytest.mark.parametrize('tool,price,current,token', [
    ('long_position', 102., '+50.00 U · +2.00%', 'positive'),
    ('long_position', 98., '-50.00 U · -2.00%', 'negative'),
    ('short_position', 98., '+50.00 U · +2.00%', 'positive'),
    ('short_position', 102., '-50.00 U · -2.00%', 'negative'),
    ('short_position', 100., '+0.00 U · +0.00%', 'text'),
    ('long_position', None, '— U · —%', 'text'),
])
def test_position_has_translucent_areas_and_values_on_lines(
    position_chart: CandleChart, tool: str, price: float | None, current: str, token: str,
) -> None:
    chart = position_chart
    chart.last_price = price
    obj = position_object(chart, tool)
    image = object_image(chart, obj)
    target, stop = obj['anchors'][1][1], obj['anchors'][2][1]
    assert_line_text(chart, obj, image, target, '+250.00 U · 10.00%', 'positive')
    assert_line_text(chart, obj, image, stop, '-125.00 U · 5.00%', 'negative')
    assert_line_text(chart, obj, image, 100., current, token)
    for boundary, background_token in ((target, 'positive'), (stop, 'negative')):
        y = chart.price_y((boundary+100)/2)
        area = QRectF(110, y-6, 580, 12).toAlignedRect()
        assert all(image.pixelColor(x, row).alpha() == 32
                   for row in range(area.top(), area.bottom()+1)
                   for x in range(area.left(), area.right()+1))
        actual, expected_color = image.pixelColor(300, round(y)), QColor(color(background_token))
        assert abs(actual.red()-expected_color.red()) <= 4
        assert abs(actual.green()-expected_color.green()) <= 4
        assert abs(actual.blue()-expected_color.blue()) <= 4
        assert image.pixelColor(90, round(y)).alpha() == 0
    # 标签周围只有区域本身的淡色，没有不透明的文字底色。
    assert image.pixelColor(400, ceil(chart.price_y(100)+chart.fontMetrics().height()/2+3)).alpha() == 32
    assert chart.hit_paths[obj['id']].contains(QPointF(300, chart.price_y((target+100)/2)))


def test_position_quote_refresh_invalidates_layer_and_marks_stale(position_chart: CandleChart) -> None:
    chart = position_chart
    obj = position_object(chart, 'long_position')
    chart.objects = [obj]
    chart.last_price = 102.
    chart.object_layers()
    first = chart._objects_layer.toImage()
    builds = chart.object_builds
    chart.object_layers()
    assert chart.object_builds == builds
    chart.last_price = 98.
    chart.object_layers()
    assert chart.object_builds == builds+1
    assert chart._objects_layer.toImage() != first
    chart.price_stale = True
    chart.object_layers()
    assert chart.object_builds == builds+2
    assert_line_text(chart, obj, object_image(chart, obj), 100., '-50.00 U · -2.00% · 过期', 'text_muted')
    chart.objects = [drawing('horizontal', [[obj['anchors'][0][0], 100.]])]
    chart.object_layers()
    builds = chart.object_builds
    chart.last_price = 103.
    chart.object_layers()
    assert chart.object_builds == builds
