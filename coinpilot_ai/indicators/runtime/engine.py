"""统一运行接口。借用输入 K 线，只保存指标自身状态与输出缓冲区。"""
from collections.abc import Sequence
from dataclasses import replace

from coinpilot_ai.market.intervals import BARS
from ..builtin.engine import _BuiltinState
from ..catalog import BUILTIN_CATALOG, IndicatorCatalog
from ..models import IndicatorInstance
from ..validation import legacy_spec, validate_inputs
from ..script.runtime import ScriptExecutor
from .result import IndicatorResult, IndicatorResultMetadata

type CandleInput = Sequence[int | float | str]


class IndicatorRuntime:
    def __init__(self, instance: IndicatorInstance, *, bar: str | None = None,
                 catalog: IndicatorCatalog = BUILTIN_CATALOG) -> None:
        self.instance = instance
        self.definition = catalog.resolve(instance.definition, instance.definition_revision)
        self.bar = bar
        if bar is not None and bar not in BARS:
            raise ValueError("无效的指标 K 线周期")
        inputs = validate_inputs(self.definition, instance.inputs)
        self._executor: _BuiltinState | ScriptExecutor
        if self.definition.source == "builtin":
            spec = legacy_spec({"kind": self.definition.name, "params": inputs}, catalog)
            self._executor = _BuiltinState(spec, bar=bar)
        else:
            self._executor = ScriptExecutor(catalog.program(self.definition.id, self.definition.revision), inputs, bar=bar)
        self.result = self._publish(self._executor.result)

    @property
    def processed_rows(self) -> int:
        return self._executor.processed_rows

    @property
    def times(self) -> Sequence[int]:
        return self._executor.times

    @property
    def confirmed(self) -> Sequence[int]:
        return self._executor.confirmed

    def _publish(self, result: IndicatorResult) -> IndicatorResult:
        return replace(result, definition_id=self.definition.id, revision=self.definition.revision,
                       metadata=IndicatorResultMetadata({out.id: out.type for out in self.definition.outputs}, self.bar))

    def update(self, rows: Sequence[CandleInput], start: int, *, dropped: int = 0) -> IndicatorResult:
        if (isinstance(start, bool) or not isinstance(start, int) or start < 0
                or isinstance(dropped, bool) or not isinstance(dropped, int) or dropped < 0):
            raise ValueError("指标更新位置无效")
        self.result = self._publish(self._executor.update(rows, start, dropped=dropped))
        return self.result


def evaluate(instance: IndicatorInstance, rows: Sequence[CandleInput], *, bar: str | None = None,
             catalog: IndicatorCatalog = BUILTIN_CATALOG) -> IndicatorResult:
    runtime = IndicatorRuntime(instance, bar=bar, catalog=catalog)
    result = runtime.update(rows, 0)
    return replace(result, times=tuple(result.times), confirmed=tuple(result.confirmed),
                   lines={key: tuple(values) for key, values in result.lines.items()})
