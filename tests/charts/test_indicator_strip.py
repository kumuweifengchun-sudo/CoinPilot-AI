"""副图图例跟随主、副图光标，保留零值及缺失值语义。"""
from collections.abc import Iterator

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from coinpilot_ai.charts.canvas import CandleChart
from coinpilot_ai.charts.indicator_strip import IndicatorStrip
from coinpilot_ai.indicators.models import PlotDefinition
from coinpilot_ai.indicators.runtime.result import IndicatorResult, IndicatorResultMetadata


@pytest.fixture
def strip(app: QApplication) -> Iterator[IndicatorStrip]:
    canvas = CandleChart()
    canvas.resize(900, 400)
    canvas.set_data([[1700000000000 + i * 900000, 100., 110., 90., 100., 10., 0., 0., 1] for i in range(5)])
    pane = IndicatorStrip(canvas, "atr")
    pane.resize(900, 112)
    yield pane
    pane.deleteLater()
    canvas.deleteLater()
    app.processEvents()


def move_to(widget: CandleChart | IndicatorStrip, point: QPointF) -> None:
    event = QMouseEvent(QEvent.Type.MouseMove, point, point, Qt.MouseButton.NoButton,
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("source", ["main", "pane"])
@pytest.mark.parametrize("offset", [.1, .5, .9])
def test_legend_follows_same_candle_across_its_width(strip: IndicatorStrip, source: str, offset: float) -> None:
    canvas = strip.canvas
    assert canvas.left_time is not None
    result = IndicatorResult("ATR", canvas.times, {"value": (None, 12.5, 0., 34.5, 56.)}, (1,) * 5)
    plots = [PlotDefinition("value", "ATR", "value", target="pane")]
    bounds = canvas.plot_rects()[0] if source == "main" else strip.plot_bounds()
    stamp = canvas.times[1] + canvas.interval * offset
    point = QPointF(bounds.left() + (stamp - canvas.left_time) / (canvas.count * canvas.interval) * bounds.width(),
                    bounds.center().y())
    move_to(canvas if source == "main" else strip, point)
    assert strip.legend_values(result, plots) == ["12.5"]
    QApplication.sendEvent(canvas if source == "main" else strip, QEvent(QEvent.Type.Leave))
    assert strip.legend_values(result, plots) == ["56"]


def test_legend_keeps_warmup_zero_and_missing_tail_distinct(strip: IndicatorStrip) -> None:
    times = strip.canvas.times
    result = IndicatorResult("ATR", times, {"value": (None, 12.5, 0., 34.5, None)}, (1,) * 5)
    plots = [PlotDefinition("value", "ATR", "value", target="pane")]
    for index, expected in ((0, "—"), (2, "0")):
        strip.show_crosshair(times[index] + strip.canvas.interval / 2)
        assert strip.legend_values(result, plots) == [expected]
    strip.show_crosshair(None)
    assert strip.legend_values(result, plots) == ["—"]
    stale = IndicatorResult("ATR", times[:2], {"value": (None, 12.5)}, (1, 1))
    assert strip.legend_values(stale, plots) == ["—"]


def test_legend_reads_multiple_outputs_and_boolean_metadata(strip: IndicatorStrip) -> None:
    times = strip.canvas.times
    result = IndicatorResult("custom", times, {"macd": (1.,) * 5, "signal": (2.,) * 5,
                             "hist": (-1.,) * 5, "buy": (0.,) * 5}, (1,) * 5,
                             metadata=IndicatorResultMetadata(output_types={"buy": "bool"}))
    plots = [PlotDefinition(key, key, key, target="pane") for key in ("macd", "signal", "hist", "buy")]
    assert strip.legend_values(result, plots) == ["1", "2", "-1", "假"]
