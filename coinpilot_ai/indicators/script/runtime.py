"""有界逐节点执行，末根修正回退状态而不重算历史。"""
from array import array
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import math
from typing import overload

from coinpilot_ai.market.buffer import candle
from coinpilot_ai.market.intervals import contiguous
from coinpilot_ai.market.rolling import RollingExtreme, RollingMoments
from ..models import InputValue
from ..runtime.context import BarContext
from ..runtime.result import IndicatorResult, IndicatorResultMetadata
from ..runtime.series import NumericLine
from ..validation import validate_inputs
from .diagnostics import ScriptError
from .ir import Instruction, Program

type Scalar = float | int | bool | str | None
type Value = Scalar | tuple[Scalar, ...]
type Average = tuple[int, float, float | None]


class BooleanLine(Sequence[bool | None]):
    def __init__(self) -> None:
        self.data = array("B")

    def __len__(self) -> int:
        return len(self.data)

    @overload
    def __getitem__(self, index: int) -> bool | None: ...
    @overload
    def __getitem__(self, index: slice) -> list[bool | None]: ...
    def __getitem__(self, index: int | slice) -> bool | None | list[bool | None]:
        if isinstance(index, slice):
            return [None if value == 2 else bool(value) for value in self.data[index]]
        value = self.data[index]
        return None if value == 2 else bool(value)

    def append(self, value: bool | None) -> None:
        self.data.append(2 if value is None else int(value))

    def __delitem__(self, index: int | slice) -> None:
        del self.data[index]


@dataclass
class NodeState:
    averages: dict[str, Average] = field(default_factory=dict)
    windows: dict[str, RollingMoments | RollingExtreme] = field(default_factory=dict)
    previous_close: float | None = None

    def copy(self) -> "NodeState":
        return NodeState(self.averages.copy(), self.windows.copy(), self.previous_close)

    def average(self, name: str, value: float | None, period: int, *, wilder: bool = False) -> float | None:
        if value is None:
            return None
        count, total, previous = self.averages.get(name, (0, 0., None))
        count += 1
        if wilder:
            if previous is None:
                total += value
                if count == period:
                    previous = total / period
            else:
                previous = (previous * (period - 1) + value) / period
        else:
            previous = value if previous is None else previous + 2 / (period + 1) * (value - previous)
        self.averages[name] = count, total, previous
        return previous if count >= period else None

    def moments(self, name: str, value: float | None, period: int) -> tuple[float | None, float | None]:
        window = self.windows.get(name)
        if window is None:
            window = RollingMoments(period)
            self.windows[name] = window
        if not isinstance(window, RollingMoments):
            raise ValueError("滚动节点类型不匹配")
        result = window.push(value)
        mean = float(result) if isinstance(result, (int, float)) else None
        return mean, math.sqrt(max(0., window.m2) / period) if mean is not None else None


def numeric(value: Value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("此操作需要数值输入")
    return float(value) if math.isfinite(value) else None


def period_value(value: Value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not 1 <= value <= 10000:
        raise ValueError("TA 周期须为 1—10000 的整数")
    return int(value)


class ScriptExecutor:
    def __init__(self, program: Program, inputs: Mapping[str, InputValue], *, bar: str | None = None) -> None:
        self.program = program
        self.inputs = validate_inputs(program.definition, inputs)
        self.bar = bar
        self.processed_rows = 0
        self.times = array("q")
        self.confirmed = array("B")
        self.lines: dict[str, NumericLine | BooleanLine] = {}
        self.states: list[NodeState] = []
        self.past: list[deque[Value]] = []
        self.variables: dict[int, Value] = {}
        self.before_states: list[NodeState] = []
        self.before_past: list[deque[Value]] = []
        self.before_variables: dict[int, Value] = {}
        self.evicted: list[tuple[bool, Value]] = []
        self.bar_count = self.before_bar_count = 0
        self._reset()
        self.result = self._result()

    def _reset(self) -> None:
        self.times, self.confirmed = array("q"), array("B")
        self.lines = {output.id: BooleanLine() if output.type == "bool" else NumericLine()
                      for output in self.program.definition.outputs}
        self.states = [NodeState() for _ in self.program.instructions]
        self.past = [deque(maxlen=limit) for limit in self.program.history_limits]
        self.variables = {}
        self.bar_count = 0

    def _result(self) -> IndicatorResult:
        definition = self.program.definition
        return IndicatorResult(definition.name, self.times, self.lines, self.confirmed, definition.id,
            definition.revision, IndicatorResultMetadata({item.id: item.type for item in definition.outputs}, self.bar))

    def _prior(self, ref: int, offset: int = 1) -> Value:
        history = self.past[ref]
        return history[-offset] if offset <= len(history) else None

    def _ta(self, index: int, node: Instruction, values: list[Value], context: BarContext) -> Value:
        name = node.op
        args = [values[ref] for ref in node.args]
        state = self.states[index]
        if name in ("ta.cross", "ta.crossover", "ta.crossunder"):
            a, b = numeric(args[0]), numeric(args[1])
            old_a, old_b = numeric(self._prior(node.args[0])), numeric(self._prior(node.args[1]))
            if a is None or b is None or old_a is None or old_b is None:
                return None
            above, below = old_a <= old_b and a > b, old_a >= old_b and a < b
            return above or below if name == "ta.cross" else above if name == "ta.crossover" else below
        if name in ("ta.tr", "ta.atr"):
            row = context.row
            high, low, close = row[2], row[3], row[4]
            previous = state.previous_close
            state.previous_close = close
            tr = high - low if previous is None else max(high - low, abs(high - previous), abs(low - previous))
            return tr if name == "ta.tr" else state.average("atr", tr, period_value(args[0]), wilder=True)
        value = numeric(args[0])
        if name == "ta.macd":
            fast = state.average("fast", value, period_value(args[1]))
            slow = state.average("slow", value, period_value(args[2]))
            macd = fast - slow if fast is not None and slow is not None else None
            signal = state.average("signal", macd, period_value(args[3]))
            return macd, signal, macd - signal if macd is not None and signal is not None else None
        period = period_value(args[1])
        if name == "ta.change":
            previous = numeric(self._prior(node.args[0], period))
            return value - previous if value is not None and previous is not None else None
        if name in ("ta.ema", "ta.rma"):
            return state.average("average", value, period, wilder=name == "ta.rma")
        if name == "ta.rsi":
            previous = state.previous_close
            state.previous_close = value
            change = value - previous if value is not None and previous is not None else None
            gain = state.average("gain", max(change, 0) if change is not None else None, period, wilder=True)
            loss = state.average("loss", max(-change, 0) if change is not None else None, period, wilder=True)
            return None if gain is None or loss is None else 100. if loss == 0 else 100 - 100 / (1 + gain / loss)
        if name in ("ta.highest", "ta.lowest"):
            if value is None:
                self.states[index].windows.pop("extreme", None)
                return None
            window = state.windows.get("extreme")
            if window is None:
                window = RollingExtreme(period, maximum=name == "ta.highest")
                state.windows["extreme"] = window
            if not isinstance(window, RollingExtreme):
                raise ValueError("极值节点类型不匹配")
            result = window.push(value)
            return float(result) if isinstance(result, (int, float)) else None
        mean, deviation = state.moments("moments", value, period)
        if name == "ta.stdev":
            return deviation
        if name == "ta.bb":
            multiplier = numeric(args[2])
            distance = deviation * multiplier if deviation is not None and multiplier is not None else None
            return mean, mean + distance if mean is not None and distance is not None else None, mean - distance if mean is not None and distance is not None else None
        return mean

    def _execute(self, index: int, node: Instruction, values: list[Value], context: BarContext) -> Value:
        op = node.op
        if op == "history":
            offset = values[node.args[1]] if len(node.args) == 3 else node.value
            if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
                raise ValueError("历史节点无效")
            return values[node.args[0]] if offset == 0 else self._prior(node.args[-1], offset)
        args = [values[ref] for ref in node.args]
        if op == "constant":
            return node.value
        if op == "source":
            if not isinstance(node.value, str):
                raise ValueError("行情节点无效")
            return context.source(node.value)
        if op == "input":
            if not isinstance(node.value, str):
                raise ValueError("输入节点无效")
            value = self.inputs[node.value]
            definition = next(item for item in self.program.definition.inputs if item.id == node.value)
            return context.source(value) if definition.type == "source" and isinstance(value, str) else value
        if op == "select":
            if args[0] is None:
                return None
            if not isinstance(args[0], bool):
                raise ValueError("条件表达式需要布尔值")
            return args[1] if args[0] else args[2]
        if op == "var":
            return self.variables.get(index, args[0])
        if op == "commit":
            self.variables[node.args[0]] = args[1]
            return None
        if op == "extract":
            value, offset = args[0], node.value
            if not isinstance(value, tuple) or not isinstance(offset, int) or offset >= len(value):
                raise ValueError("元组输出无效")
            return value[offset]
        if op == "na":
            return args[0] is None
        if op == "nz":
            return args[0] if args[0] is not None else args[1] if len(args) > 1 else 0.
        if op.startswith("ta."):
            return self._ta(index, node, values, context)
        if op in ("and", "or", "not"):
            if any(arg is not None and not isinstance(arg, bool) for arg in args):
                raise ValueError("逻辑操作需要布尔值")
            if op == "not":
                return None if args[0] is None else not args[0]
            if op == "and" and any(arg is False for arg in args):
                return False
            if op == "or" and any(arg is True for arg in args):
                return True
            return None if any(arg is None for arg in args) else all(args) if op == "and" else any(args)
        if op in ("==", "!="):
            return None if any(arg is None for arg in args) else args[0] == args[1] if op == "==" else args[0] != args[1]
        numbers = [numeric(arg) for arg in args]
        if any(value is None for value in numbers):
            return None
        numbers_present = [value for value in numbers if value is not None]
        a = numbers_present[0]
        if op in ("+", "-") and len(args) == 1:
            return a if op == "+" else -a
        if op.startswith("math."):
            if op == "math.abs":
                return abs(a)
            if op == "math.sqrt":
                return math.sqrt(a) if a >= 0 else None
            if op == "math.round":
                return math.floor(a + .5)
            if op == "math.max":
                return max(numbers_present)
            if op == "math.min":
                return min(numbers_present)
            raise ValueError("未知的数学操作")
        b = numbers_present[1]
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b if b else None
        if op == "%":
            return math.fmod(a, b) if b else None
        if op == ">":
            return a > b
        if op == "<":
            return a < b
        if op == ">=":
            return a >= b
        if op == "<=":
            return a <= b
        raise ValueError("未知的执行节点")

    def _append(self, row: Sequence[int | float | str]) -> None:
        context = BarContext(candle(row), self.bar_count)
        if self.times and context.row[0] <= self.times[-1]:
            raise ValueError("K 线时间须严格递增")
        self.before_states = [state.copy() for state in self.states]
        self.before_variables = self.variables.copy()
        self.before_past = self.past
        self.before_bar_count = self.bar_count
        if self.times and self.bar is not None and (not contiguous(self.times[-1], context.row[0], self.bar) or not self.confirmed[-1]):
            self.states = [NodeState() for _ in self.program.instructions]
            self.past = [deque(maxlen=limit) for limit in self.program.history_limits]
            self.variables = {}
        values: list[Value] = []
        for index, node in enumerate(self.program.instructions):
            try:
                value = self._execute(index, node, values, context)
                if isinstance(value, float) and not math.isfinite(value):
                    value = None
                values.append(value)
            except (ValueError, OverflowError, ZeroDivisionError) as exc:
                raise ScriptError(node.line, node.column, "RUNTIME", str(exc)) from None
        self.evicted = []
        for history, value in zip(self.past, values):
            evicted = bool(history.maxlen and len(history) == history.maxlen)
            self.evicted.append((evicted, history[0] if evicted else None))
            history.append(value)
        for name, ref in self.program.outputs:
            output = values[ref]
            buffer = self.lines[name]
            if isinstance(buffer, BooleanLine):
                if output is not None and not isinstance(output, bool):
                    raise ValueError("布尔输出类型无效")
                buffer.append(output)
            else:
                buffer.append(numeric(output))
        self.times.append(context.row[0])
        self.confirmed.append(context.row[8])
        self.bar_count += 1
        self.processed_rows += 1

    def _rollback(self) -> None:
        for state in self.states:
            for window in state.windows.values():
                window.undo()
        self.states = [state.copy() for state in self.before_states]
        self.variables = self.before_variables.copy()
        if self.past is self.before_past:
            for history, (evicted, value) in zip(self.past, self.evicted):
                history.pop()
                if evicted:
                    history.appendleft(value)
        else:
            self.past = self.before_past
        self.bar_count = self.before_bar_count

    def update(self, rows: Sequence[Sequence[int | float | str]], start: int, *, dropped: int = 0) -> IndicatorResult:
        if dropped:
            for buffer in (self.times, self.confirmed, *self.lines.values()):
                del buffer[:dropped]
        size = len(self.times)
        rebuild = (start < max(0, size - 1) or start > size or len(rows) < size or
                   start > 0 and int(rows[start - 1][0]) != self.times[start - 1])
        if rebuild or start == 0:
            self._reset()
            start = 0
        elif start < size:
            self._rollback()
        del self.times[start:]
        del self.confirmed[start:]
        for values in self.lines.values():
            del values[start:]
        for index in range(start, len(rows)):
            self._append(rows[index])
        self.result = self._result()
        return self.result
