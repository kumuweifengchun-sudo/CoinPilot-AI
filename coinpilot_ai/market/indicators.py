"""旧指标接口的兼容适配层；算法与元数据由 indicators 平台持有。"""
from bisect import bisect_left
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import replace

from coinpilot_ai.indicators.catalog import BUILTIN_CATALOG, IndicatorCatalog
from coinpilot_ai.indicators.models import IndicatorCacheKey, IndicatorInstance
from coinpilot_ai.indicators.runtime.engine import CandleInput, IndicatorRuntime
from coinpilot_ai.indicators.runtime.result import IndicatorResult as IndicatorResult
from coinpilot_ai.indicators.runtime.series import NumericLine as NumericLine
from coinpilot_ai.indicators.validation import LegacySpec, instance_from_record, legacy_spec
from .buffer import CandleBuffer, candle

# 只保留旧调用方的快照；新消费者向 Catalog 查询定义。
KINDS = {definition.name for definition in BUILTIN_CATALOG.list()}
DEFAULTS = {definition.name: {item.id: int(item.default) if isinstance(item.default, int) else float(item.default)
                              for item in definition.inputs if isinstance(item.default, (int, float))}
            for definition in BUILTIN_CATALOG.list()}


def validate_spec(spec: object) -> LegacySpec:
    return legacy_spec(spec)


def _instance(spec: object, catalog: IndicatorCatalog) -> IndicatorInstance:
    if isinstance(spec, IndicatorInstance):
        return spec
    if not isinstance(spec, dict):
        raise ValueError("指标配置须为对象")
    return instance_from_record(dict(spec, id=spec.get("id", "legacy-runtime")), catalog)


def calculate(rows: Sequence[CandleInput], spec: object, *, bar: str | None = None,
              catalog: IndicatorCatalog = BUILTIN_CATALOG) -> IndicatorResult:
    state = IndicatorState(spec, bar=bar, catalog=catalog)
    result = state.update(rows, 0)
    return replace(result, times=tuple(result.times), confirmed=tuple(result.confirmed),
                   lines={key: tuple(values) for key, values in result.outputs.items()})


class IndicatorState:
    """旧 State 接口复用统一运行时，保留尾部回退与滚出前缀行为。"""
    RECURSIVE = {"EMA", "RSI", "MACD", "ATR", "SUPERTREND", "VWAP"}

    def __init__(self, spec: object, *, bar: str | None = None,
                 catalog: IndicatorCatalog = BUILTIN_CATALOG) -> None:
        self.instance = _instance(spec, catalog)
        self.runtime = IndicatorRuntime(self.instance, bar=bar, catalog=catalog)
        self.kind = self.runtime.definition.name
        self.params = dict(self.instance.inputs)
        self.spec: object = spec

    @property
    def result(self) -> IndicatorResult:
        return self.runtime.result

    @property
    def processed_rows(self) -> int:
        return self.runtime.processed_rows

    @property
    def times(self) -> Sequence[int]:
        return self.runtime.times

    @property
    def confirmed(self) -> Sequence[int]:
        return self.runtime.confirmed

    @property
    def lines(self) -> Mapping[str, Sequence[float | None]]:
        return self.result.lines

    def update(self, rows: Sequence[CandleInput], start: int, *, dropped: int = 0) -> IndicatorResult:
        return self.runtime.update(rows, start, dropped=dropped)


class IndicatorStream:
    """逐根输入的有限窗口；只消费已提供的数据，不读取未来行情。"""
    def __init__(self, spec: object, bar: str, limit: int = 2048, *,
                 catalog: IndicatorCatalog = BUILTIN_CATALOG) -> None:
        if isinstance(limit, bool) or limit < 2:
            raise ValueError("指标流窗口至少两根 K 线")
        self.rows = CandleBuffer()
        self.state = IndicatorState(spec, bar=bar, catalog=catalog)
        self.limit = limit

    def push(self, row: CandleInput) -> IndicatorResult:
        value = candle(row)
        if self.rows and self.rows[-1][0] == value[0]:
            self.rows[-1] = value
        else:
            self.rows.append(value)
        dropped = len(self.rows) - self.limit + self.limit // 4 if len(self.rows) > self.limit else 0
        if dropped:
            del self.rows[:dropped]
        return self.state.update(self.rows, len(self.rows) - 1, dropped=dropped)


class IndicatorRegistry:
    """提醒和扫描的已收盘状态缓存；定义版本、输入和周期均属于缓存键。"""
    def __init__(self, limit: int = 128, *, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> None:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("指标缓存数量须大于零")
        self.items: OrderedDict[tuple[tuple[str, str], IndicatorCacheKey], IndicatorStream] = OrderedDict()
        self.limit = limit
        self.catalog = catalog

    def get(self, pair: tuple[str, str], rows: Sequence[CandleInput], spec: object) -> IndicatorResult:
        instance = _instance(spec, self.catalog)
        key = pair, IndicatorCacheKey.of(instance, pair[1])
        stream = self.items.get(key)
        times = rows.times if isinstance(rows, CandleBuffer) else [int(row[0]) for row in rows]
        start = 0
        if stream is not None and stream.rows:
            stamp = stream.rows[-1][0]
            index = bisect_left(times, stamp)
            newest = next((int(row[0]) for row in reversed(rows) if int(row[8]) == 1), None)
            if newest is not None and stamp <= newest and index < len(rows) and int(rows[index][0]) == stamp:
                start = index
            else:
                stream = None
        if stream is None:
            stream = IndicatorStream(instance, pair[1], catalog=self.catalog)
        for index in range(start, len(rows)):
            row = rows[index]
            if int(row[8]) == 1 and (not stream.rows or tuple(row) != stream.rows[-1]):
                stream.push(row)
        self.items[key] = stream
        self.items.move_to_end(key)
        while len(self.items) > self.limit:
            self.items.popitem(last=False)
        return stream.state.result
