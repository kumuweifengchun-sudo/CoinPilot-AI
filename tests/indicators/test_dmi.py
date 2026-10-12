"""DMI 的手算数值、Wilder 增量状态与内置界面入口。"""
from pathlib import Path
import random

import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QSpinBox

from coinpilot_ai.charts.settings import IndicatorEditDialog, IndicatorSettingsDialog
from coinpilot_ai.charts.state import ChartBook
from coinpilot_ai.core.store import Store
from coinpilot_ai.indicators.builtin.engine import _reference
from coinpilot_ai.indicators.catalog import BUILTIN_CATALOG
from coinpilot_ai.indicators.models import IndicatorInstance
from coinpilot_ai.indicators.runtime.engine import IndicatorRuntime
from coinpilot_ai.indicators.validation import instance_from_record, legacy_spec
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.indicators import IndicatorRegistry, calculate
from coinpilot_ai.trading.dialogs import IndicatorConditionDialog


def path_rows(direction: int, count: int = 100) -> CandleBuffer:
    return CandleBuffer((300000+i*60000, 500.+direction*i+1, 500.+direction*i+2,
                         500.+direction*i, 500.+direction*i+1, 10., 0., 0., 1) for i in range(count))


@pytest.mark.parametrize("direction,expected", [(1, (50., 0., 100.)), (-1, (0., 50., 100.)), (0, (0., 0., 0.))])
def test_known_direction_and_exact_warmup(direction: int, expected: tuple[float, float, float]) -> None:
    result = calculate(path_rows(direction), {"kind": "DMI"}, bar="1m")
    assert result.lines["plus_di"][:14] == (None,) * 14
    assert result.lines["adx"][:27] == (None,) * 27
    for key, value in zip(("plus_di", "minus_di", "adx"), expected):
        assert result.lines[key][-1] == pytest.approx(value)
    assert result.lines["adx"][27] == pytest.approx(expected[2])


def test_hand_calculated_wilder_seed_recurrence_and_tied_moves() -> None:
    rows = CandleBuffer((300000+i*60000, close, high, low, close, 1., 0., 0., 1)
                        for i, (high, low, close) in enumerate(((12., 8., 10.), (15., 9., 12.),
                                                              (14., 7., 11.), (16., 9., 14.), (17., 8., 12.))))
    result = calculate(rows, {"kind": "DMI", "params": {"period": 2, "adx_period": 2}}, bar="1m")
    assert result.lines["plus_di"] == pytest.approx([None, None, 100*1.5/6.5, 100*1.75/6.75, 100*.875/7.875])
    assert result.lines["minus_di"] == pytest.approx([None, None, 100/6.5, 100*.5/6.75, 100*.25/7.875])
    assert result.lines["adx"] == pytest.approx([None, None, None, 340/9, 140/3])
    flat = CandleBuffer((300000+i*60000, 10., 10., 10., 10., 0., 0., 0., 1) for i in range(5))
    zero = calculate(flat, {"kind": "DMI", "params": {"period": 1, "adx_period": 1}}, bar="1m")
    assert all(values == (None, 0., 0., 0., 0.) for values in zero.lines.values())


@pytest.mark.parametrize("period,adx_period", [(1, 1), (3, 5), (14, 14)])
def test_incremental_tail_correction_rollover_and_gaps_match_batch_reference(period: int, adx_period: int) -> None:
    rng = random.Random(41)
    rows = CandleBuffer((300000+i*60000, 100., 101.+rng.random()*8, 99.-rng.random()*8,
                         100., 10., 0., 0., 1) for i in range(90))
    spec = {"kind": "DMI", "params": {"period": period, "adx_period": adx_period}}
    runtime = IndicatorRuntime(IndicatorInstance("dmi", "builtin.dmi", 1, spec["params"]), bar="1m")
    for size in range(1, len(rows)):
        result = runtime.update(rows[:size], size-1)
        reference = _reference(rows[:size], spec, bar="1m")
        for key in reference.lines:
            assert list(result.lines[key]) == pytest.approx(reference.lines[key])
    buffers = dict(runtime.result.lines)
    for high in (120., 101., 115.):
        original = rows[88]
        rows[88] = (original[0], 100., high, 95., 100., 10., 0., 0., 0)
        before = runtime.processed_rows
        result = runtime.update(rows[:89], 88)
        assert runtime.processed_rows == before+1
        assert all(result.lines[key] is values for key, values in buffers.items())
        for key, values in _reference(rows[:89], spec, bar="1m").lines.items():
            assert list(result.lines[key]) == pytest.approx(values)
    # 未收盘前根不能作为下一根方向变化基线。
    result = runtime.update(rows, 89)
    assert all(result.lines[key][-1] is None for key in result.lines)
    rows[88] = (*rows[88][:8], 1)
    runtime.update(rows, 0)
    result = runtime.update(rows[20:], 69, dropped=20)
    for key, values in _reference(rows, spec, bar="1m").lines.items():
        assert list(result.lines[key]) == pytest.approx(values[20:])
    del rows[40]
    result = runtime.update(rows, 0)
    for key, values in _reference(rows, spec, bar="1m").lines.items():
        assert list(result.lines[key]) == pytest.approx(values)
    assert result.lines["plus_di"][40] is None


@pytest.mark.parametrize("key", ["period", "adx_period"])
@pytest.mark.parametrize("value", [0, 1001, 1.5, True])
def test_invalid_period_is_rejected(key: str, value: object) -> None:
    with pytest.raises(ValueError):
        legacy_spec({"kind": "DMI", "params": {key: value}})


def test_builtin_search_styles_parameters_and_signal_outputs(app: QApplication, tmp_path: Path) -> None:
    store = Store(tmp_path / "dmi.db")
    book = ChartBook(store)
    settings = IndicatorSettingsDialog(book)
    try:
        settings.search.setText("动向")
        visible = [item for index in range(settings.available.count())
                   if (item := settings.available.item(index)) is not None and not item.isHidden()]
        assert len(visible) == 1 and "DMI" in visible[0].text()
        settings.available.setCurrentItem(visible[0])
        settings.add()
        editor = IndicatorEditDialog(settings.items[-1])
        try:
            assert editor.input_fields.values() == {"period": 14, "adx_period": 14}
            assert {shade.text() for _, shade, _ in editor.plot_fields.values()} == {"#20c997", "#f06472", "#f5a623"}
            period = editor.params["adx_period"]
            assert isinstance(period, QSpinBox)
            period.setValue(6)
            editor.save()
            assert editor.result() == QDialog.DialogCode.Accepted
            book.save_indicators([editor.item])
            restored = instance_from_record(ChartBook(store).indicators[0])
            assert restored.definition == "builtin.dmi" and restored.inputs["adx_period"] == 6
        finally:
            editor.deleteLater()
        assert BUILTIN_CATALOG.get("builtin.dmi").pane.mode == "pane"
        for output in ("plus_di", "minus_di", "adx"):
            dialog = IndicatorConditionDialog({"indicator": {"kind": "DMI"}, "line": output, "op": "above", "threshold": "25"})
            dialog.save()
            assert dialog.condition is not None and dialog.condition["line"] == output
            dialog.deleteLater()
        rows = path_rows(1)
        rows[-1] = (*rows[-1][:8], 0)
        result = IndicatorRegistry().get(("BTC-USDT-SWAP", "1m"), rows, {"kind": "DMI"})
        assert result.times[-1] == rows[-2][0] and all(result.confirmed)
    finally:
        settings.deleteLater()
        store.close()
