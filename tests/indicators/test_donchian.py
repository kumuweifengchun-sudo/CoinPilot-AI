"""唐奇安通道的独立窗口数值、回退、缺口与界面入口回归。"""
from dataclasses import dataclass, field

import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from coinpilot_ai.charts.settings import IndicatorEditDialog, IndicatorSettingsDialog
from coinpilot_ai.charts.state import IndicatorSetting, normalize_indicators
from coinpilot_ai.indicators.catalog import BUILTIN_CATALOG, IndicatorCatalog
from coinpilot_ai.indicators.models import IndicatorInstance
from coinpilot_ai.indicators.runtime.engine import IndicatorRuntime
from coinpilot_ai.indicators.validation import instance_from_record, instance_record, legacy_spec
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.indicators import IndicatorRegistry, calculate
from coinpilot_ai.trading.dialogs import IndicatorConditionDialog


def candles() -> CandleBuffer:
    highs = (12., 15., 13., 11., 14., 16., 13., 12., 14.)
    lows = (8., 9., 7., 6., 8., 9., 5., 8., 7.)
    return CandleBuffer((300000 + i*60000, 10., high, low, 10., 10., 0., 0., 1)
                        for i, (high, low) in enumerate(zip(highs, lows)))


def expected(data: CandleBuffer, period: int) -> dict[str, list[float | None]]:
    upper: list[float | None] = []
    middle: list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(data)):
        window = data[max(0, i-period+1):i+1]
        high = max(row[2] for row in window) if len(window) == period else None
        low = min(row[3] for row in window) if len(window) == period else None
        upper.append(high)
        lower.append(low)
        middle.append((high+low)/2 if high is not None and low is not None else None)
    return {"upper": upper, "middle": middle, "lower": lower}


@pytest.mark.parametrize("period", [1, 3, 20])
def test_window_includes_current_bar_and_tail_corrections_restore_extremes(period: int) -> None:
    data = candles()
    runtime = IndicatorRuntime(IndicatorInstance("channel", "builtin.donchian", 1, {"period": period}), bar="1m")
    for size in range(1, len(data)+1):
        result = runtime.update(data[:size], size-1)
        for key, values in expected(data[:size], period).items():
            assert list(result.lines[key]) == pytest.approx(values)
    original = data[-1]
    for high, low in ((30., 1.), (11., 9.), (14., 7.)):
        data[-1] = (original[0], 10., high, low, 10., 10., 0., 0., 0)
        before = runtime.processed_rows
        result = runtime.update(data, len(data)-1)
        assert runtime.processed_rows == before+1
        assert not result.confirmed[-1]
        for key, values in expected(data, period).items():
            assert list(result.lines[key]) == pytest.approx(values)


def test_rolling_out_prefix_preserves_channel_window() -> None:
    data = candles()
    runtime = IndicatorRuntime(IndicatorInstance("channel", "builtin.donchian", 1, {"period": 3}), bar="1m")
    runtime.update(data[:8], 0)
    before = runtime.processed_rows
    result = runtime.update(data[3:], 5, dropped=3)
    assert runtime.processed_rows == before+1
    for key, values in expected(data, 3).items():
        assert list(result.lines[key]) == pytest.approx(values[3:])


def test_gap_and_unconfirmed_bar_restart_warmup_and_registry_ignores_open_bar() -> None:
    data = candles()
    del data[3]
    result = calculate(data, {"kind": "DONCHIAN", "params": {"period": 3}}, bar="1m")
    for key, values in expected(data[3:], 3).items():
        assert list(result.lines[key])[3:] == pytest.approx(values)
    row = data[5]
    data[5] = (*row[:8], 0)
    result = calculate(data, {"kind": "DONCHIAN", "params": {"period": 3}}, bar="1m")
    assert all(result.lines[key][6] is None for key in result.lines)
    full = candles()
    full[-1] = (*full[-1][:8], 0)
    closed = IndicatorRegistry().get(("BTC-USDT-SWAP", "1m"), full, {"kind": "DONCHIAN"})
    assert closed.times[-1] == full[-2][0]
    assert all(closed.confirmed)


@pytest.mark.parametrize("period", [0, 1001, 1.5, True, float("nan")])
def test_period_boundary_rejects_invalid_values(period: object) -> None:
    with pytest.raises(ValueError):
        legacy_spec({"kind": "DONCHIAN", "params": {"period": period}})


@dataclass
class SettingsBook:
    catalog: IndicatorCatalog = field(default_factory=IndicatorCatalog)
    indicators: list[IndicatorSetting] = field(default_factory=list)
    indicator_error: str = ""
    service: None = None

    def save_indicators(self, items: object) -> None:
        self.indicators = normalize_indicators(items, self.catalog)


def test_chinese_search_styles_roundtrip_and_all_channel_condition_outputs(app: QApplication) -> None:
    book = SettingsBook()
    settings = IndicatorSettingsDialog(book)
    try:
        settings.search.setText("唐奇安")
        matches = [settings.available.item(i) for i in range(settings.available.count())]
        visible = [item for item in matches if item is not None and not item.isHidden()]
        assert len(visible) == 1 and "DONCHIAN" in visible[0].text()
        settings.available.setCurrentItem(visible[0])
        settings.add()
        editor = IndicatorEditDialog(settings.items[0])
        try:
            assert editor.input_fields.values() == {"period": 20}
            assert set(editor.plot_fields) == {"upper", "middle", "lower"}
            shown, shade, width = editor.plot_fields["middle"]
            shown.setChecked(False)
            shade.setText("#112233")
            width.setValue(2.5)
            editor.save()
            assert editor.result() == QDialog.DialogCode.Accepted
            instance = instance_from_record(editor.item)
            assert instance == instance_from_record(instance_record(instance))
            assert not instance.plots["middle"].visible
            assert instance.plots["middle"].color == "#112233"
        finally:
            editor.deleteLater()
        for line in ("upper", "middle", "lower"):
            dialog = IndicatorConditionDialog({"indicator": {"kind": "DONCHIAN"}, "line": line,
                                               "op": "above", "threshold": "100"})
            try:
                dialog.save()
                assert dialog.result() == QDialog.DialogCode.Accepted
                assert dialog.condition is not None and dialog.condition["line"] == line
            finally:
                dialog.deleteLater()
        assert BUILTIN_CATALOG.get("builtin.atr").inputs[0].default == 14
        assert BUILTIN_CATALOG.get("builtin.atr").pane.mode == "pane"
    finally:
        settings.deleteLater()
