from dataclasses import replace
import math

import pytest

from coinpilot_ai.indicators.builtin.engine import _reference
from coinpilot_ai.indicators.catalog import IndicatorCatalog
from coinpilot_ai.indicators.models import IndicatorInstance
from coinpilot_ai.indicators.runtime.engine import IndicatorRuntime, evaluate
from coinpilot_ai.indicators.script.compiler import compile_source, diagnostics
from coinpilot_ai.indicators.script.diagnostics import ScriptError
from coinpilot_ai.indicators.script.lexer import tokenize
from coinpilot_ai.market.buffer import CandleBuffer


def data(count: int = 140) -> CandleBuffer:
    return CandleBuffer((300000 + i * 60000, 100., 105., 95., 100. + math.sin(i / 5) * 3, 10., 0., 0., 1)
                        for i in range(count))


@pytest.mark.parametrize("source", [
    'indicator("x")\nplot(close and true)', 'indicator("x")\nplotshape(close)',
    'indicator("x")\nx=input.int(999999999999)\nplot(x)',
    'indicator("x")\nx=input.float(2, step=-1)\nplot(x)',
    'indicator("x")\nplot(close, location=location.top)',
    'indicator("x")\nplot(ta.ema(true, 3))',
])
def test_invalid_types_and_unsupported_styles_fail_compilation(source: str) -> None:
    with pytest.raises(ScriptError):
        compile_source(source)


def test_input_history_offsets_and_ta_function_parameters_change_results() -> None:
    program = compile_source('''indicator("History")
n = input.int(1)
period = input.int(3)
prior(src) => src[1]
ema(src, length) => ta.ema(src, length)
x = close[n]
y = prior(close)
z = ema(close, period)
plot(x)
plot(y)
plot(z)
''')
    catalog = IndicatorCatalog()
    catalog.register_program(program)
    rows = data(30)
    definition = program.definition
    assert definition.inputs[0].minimum == 0 and definition.inputs[0].maximum == 10000
    assert definition.inputs[1].minimum == 1 and definition.inputs[1].maximum == 10000
    for n in (0, 1, 3):
        instance = IndicatorInstance("i", definition.id, 1, {"n": n, "period": 5})
        result = evaluate(instance, rows, bar="1m", catalog=catalog)
        assert list(result.outputs["x"]) == [None]*n + [row[4] for row in rows[:len(rows)-n]]
        assert list(result.outputs["y"]) == [None] + [row[4] for row in rows[:-1]]
        reference = _reference(rows, {"kind": "EMA", "params": {"period": 5}}, bar="1m")
        assert list(result.outputs["z"]) == pytest.approx(reference.outputs["value"])


def test_reassigned_constant_period_does_not_reuse_old_input_default() -> None:
    source = '''indicator("Change")
n = input.int(1)
n := 5
x = ta.change(close, n)
y = close[n]
plot(x)
plot(y)
'''
    program = compile_source(source)
    catalog = IndicatorCatalog()
    catalog.register_program(program)
    rows = data(20)
    result = evaluate(IndicatorInstance("i", program.definition.id, 1), rows, bar="1m", catalog=catalog)
    assert list(result.outputs["x"]) == [None]*5 + [rows[i][4]-rows[i-5][4] for i in range(5, len(rows))]
    assert list(result.outputs["y"]) == [None]*5 + [r[4] for r in rows[:-5]]
    with pytest.raises(ScriptError, match="常量"):
        compile_source('indicator("x")\nn=input.int(3)\nn:=n+2\nplot(ta.change(close,n))')


@pytest.mark.parametrize("expression,kind,output", [
    ("ta.sma(close, 7)", "MA", "value"), ("ta.rsi(close, 7)", "RSI", "value"),
    ("ta.atr(7)", "ATR", "value"), ("ta.ema(volume, 7)", "EMA", "value"),
])
def test_ta_warmup_and_incremental_match_independent_builtin_reference(expression: str, kind: str, output: str) -> None:
    catalog = IndicatorCatalog()
    program = compile_source('indicator("TA")\nx=' + expression + '\nplot(x)')
    catalog.register_program(program)
    rows = data()
    if "volume" in expression:
        reference_rows = CandleBuffer((r[0], r[5], r[5]+1, r[5]-1, r[5], r[5], 0., 0., 1) for r in rows)
    else:
        reference_rows = rows
    expected = _reference(reference_rows, {"kind": kind, "params": {"period": 7}}, bar="1m").outputs[output]
    instance = IndicatorInstance("TA", program.definition.id, 1)
    actual = evaluate(instance, rows, bar="1m", catalog=catalog).outputs["x"]
    assert list(actual) == pytest.approx(expected)


@pytest.mark.parametrize("version", [5, 6])
def test_compile_execute_cross_and_boolean_history(version: int) -> None:
    source = f'''//@version={version}
indicator("EMA Cross", overlay=true)
fast = input.int(3, "Fast", minval=1, maxval=100)
slow = input.int(8)
src = input.source(close)
a = ta.ema(src, fast)
b = ta.ema(close, slow)
buy = ta.crossover(a, b) and close > low
plot(a, title="fast")
plot(b, title="slow")
plotshape(buy, title="buy", color=color.green)
plot(buy[1], title="prior buy")
'''
    program = compile_source(source)
    assert len(program.definition.inputs) == 3
    assert program.definition.outputs[2].type == "bool"
    catalog = IndicatorCatalog()
    catalog.register_program(program)
    instance = IndicatorInstance("i", program.definition.id, 1)
    runtime = IndicatorRuntime(instance, bar="1m", catalog=catalog)
    candles = data()
    for index in range(len(candles)):
        runtime.update(candles[:index + 1], index)
    result = runtime.result
    assert any(value is True for value in result.outputs["buy"])
    assert result.outputs["plot_4"][0] is None
    assert list(result.outputs["plot_4"])[1:] == list(result.outputs["buy"])[:-1]
    reference = _reference(candles, {"kind": "EMA", "params": {"period": 3}}, bar="1m")
    assert list(result.outputs["a"]) == pytest.approx(reference.outputs["value"])
    before = runtime.processed_rows
    changed = list(candles[-1])
    changed[4] = 103.
    changed[8] = 0
    candles[-1] = changed
    corrected = runtime.update(candles, len(candles) - 1)
    full = evaluate(instance, candles, bar="1m", catalog=catalog)
    assert runtime.processed_rows == before + 1
    for name in full.outputs:
        assert list(corrected.outputs[name]) == pytest.approx(full.outputs[name])
    assert not corrected.confirmed[-1]


def test_var_if_else_missing_values_and_tail_rollback() -> None:
    source = '''indicator("Count")
var count = 0
if close > open
    count := count + 1
else
    count := count - 1
plot(count)
plot(count[1])
plot(ta.sma(close, 3))
'''
    program = compile_source(source)
    catalog = IndicatorCatalog()
    catalog.register_program(program)
    instance = IndicatorInstance("i", program.definition.id, 1)
    candles = data(10)
    runtime = IndicatorRuntime(instance, bar="1m", catalog=catalog)
    result = runtime.update(candles, 0)
    assert result.outputs["count"][0] == -1
    assert list(result.outputs["plot_2"])[1:] == list(result.outputs["count"])[:-1]
    assert list(result.outputs["plot_3"])[:2] == [None, None]
    for close in (96., 104., 98.):
        row = list(candles[-1])
        row[4] = close
        candles[-1] = row
        corrected = runtime.update(candles, 9)
        full = evaluate(instance, candles, bar="1m", catalog=catalog)
        assert list(corrected.outputs["count"]) == list(full.outputs["count"])
    del candles[4]
    result = runtime.update(candles, 0)
    assert list(result.outputs["plot_3"])[4:6] == [None, None]


def test_ta_tuple_math_hline_and_function_inline() -> None:
    source = '''indicator("MACD")
twice(x) => math.abs(x) * 2
[macd, signal, histogram] = ta.macd(close, 3, 8, 2)
[middle, upper, lower] = ta.bb(close, 3, 2)
plot(macd)
plot(signal)
plot(histogram, style=plot.style_histogram)
plot(twice(upper - lower))
hline(0)
bgcolor(histogram > 0 ? color.green : na)
'''
    program = compile_source(source)
    catalog = IndicatorCatalog()
    catalog.register_program(program)
    result = evaluate(IndicatorInstance("i", program.definition.id, 1), data(), bar="1m", catalog=catalog)
    reference = _reference(data(), {"kind": "MACD", "params": {"fast": 3, "slow": 8, "signal": 2}}, bar="1m")
    for name, key in (("macd", "macd"), ("signal", "signal"), ("histogram", "histogram")):
        assert list(result.outputs[name]) == pytest.approx(reference.outputs[key])
    assert program.definition.plots[2].type == "histogram"
    assert program.definition.plots[4].value == 0
    assert program.definition.plots[5].type == "background"


@pytest.mark.parametrize("snippet,code", [
    ('request.security("BTC", "1H", close)', "UNSUPPORTED_CALL"),
    ('strategy.entry("buy", 1)', "UNSUPPORTED_CALL"),
    ('table.new(1, 2)', "UNSUPPORTED_CALL"),
    ('os.system("anything")', "UNSUPPORTED_CALL"),
    ('import os', "UNSUPPORTED_SYNTAX"),
    ('for i = 0 to 5', "UNSUPPORTED_SYNTAX"),
    ('plot(close[-1])', "HISTORY_LIMIT"),
    ('plot(close[10001])', "HISTORY_LIMIT"),
    ('plot(ta.ema(close, close))', "CONSTANT"),
    ('loop(x) => loop(x)', "RECURSION"),
])
def test_unsupported_or_dangerous_source_has_local_diagnostic(snippet: str, code: str) -> None:
    source = 'indicator("Bad")\n' + snippet + '\nplot(close)\n'
    found = diagnostics(source)
    assert found and found[0].code == code
    assert found[0].line == 2 and found[0].column > 0


def test_compile_resource_limits_and_future_data_is_unchanged() -> None:
    with pytest.raises(ScriptError, match="256KiB"):
        tokenize("//" + "x" * (256 * 1024))
    source = 'indicator("Many")\n' + 'plot(close)\n' * 33
    assert diagnostics(source)[0].code == "PLOT_LIMIT"
    source = 'indicator("Nested")\nplot(' + "(" * 101 + "close" + ")" * 101 + ')\n'
    assert diagnostics(source)[0].code == "NESTING_LIMIT"
    source = 'indicator("Past")\nplot(ta.ema(close, 3))\nplot(close[2])\n'
    program = compile_source(source)
    catalog = IndicatorCatalog()
    catalog.register_program(program)
    instance = IndicatorInstance("i", program.definition.id, 1)
    candles = data(50)
    before = evaluate(instance, candles[:30], bar="1m", catalog=catalog)
    after = evaluate(instance, candles, bar="1m", catalog=catalog)
    for name in before.outputs:
        assert before.outputs[name] == after.outputs[name][:30]
    altered = evaluate(replace(instance, inputs={}), candles, bar="1m", catalog=catalog)
    assert altered.outputs == after.outputs
