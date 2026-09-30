"""行情内部唯一行格式：int64 时间、七列 float64 和 uint8 收盘标记。"""
from __future__ import annotations

from array import array
from bisect import bisect_left
from collections.abc import MutableSequence, Sequence, Mapping
import math
from typing import TypeAlias, cast, overload

CandleRow: TypeAlias = tuple[int, float, float, float, float, float, float, float, int]
PriceValues: TypeAlias = tuple[float, float, float, float, float]


def candle(row) -> CandleRow:
    if len(row) != 9:
        raise ValueError("K 线必须有九个字段")
    stamp, values, confirmed = int(row[0]), tuple(float(v) for v in row[1:8]), int(row[8])
    op, high, low, close = values[:4]
    if (stamp <= 0 or not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0
            or min(values[4:]) < 0 or low > min(op, close) or high < max(op, close)
            or low > high or confirmed not in (0, 1)):
        raise ValueError("K 线数值无效")
    return cast(CandleRow, (stamp, *values, confirmed))


class CandleBuffer(MutableSequence[CandleRow]):
    """列式缓冲区；按行访问仅生成临时元组，不存储字符串或重复行对象。"""
    def __init__(self, rows=()):
        self.columns = [array(code) for code in ("q", *(["d"]*7), "B")]
        self.revision = 0
        self.extend(rows)

    @property
    def times(self):
        return self.columns[0]

    @property
    def nbytes(self):
        return sum(len(c)*c.itemsize for c in self.columns)

    def __len__(self) -> int:
        return len(self.times)

    @overload
    def __getitem__(self, index: int) -> CandleRow: ...

    @overload
    def __getitem__(self, index: slice) -> "CandleBuffer": ...

    def __getitem__(self, index: int | slice) -> CandleRow | "CandleBuffer":
        if isinstance(index, slice):
            result = CandleBuffer()
            result.columns = [column[index] for column in self.columns]
            return result
        return cast(CandleRow, tuple(column[index] for column in self.columns))

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            value = value if isinstance(value, CandleBuffer) else CandleBuffer(value)
            for target, source in zip(self.columns, value.columns):
                target[index] = source
        else:
            for column, number in zip(self.columns, candle(value)):
                column[index] = number
        self.revision += 1

    def __delitem__(self, index):
        for column in self.columns:
            del column[index]
        self.revision += 1

    def insert(self, index, value):
        for column, number in zip(self.columns, candle(value)):
            column.insert(index, number)
        self.revision += 1

    def append(self, value):
        for column, number in zip(self.columns, value):
            column.append(number)
        self.revision += 1

    def extend(self, rows):
        if isinstance(rows, CandleBuffer):
            for target, source in zip(self.columns, rows.columns):
                target.extend(source)
        else:
            for row in rows:
                self.append(candle(row))
        self.revision += 1

    def __eq__(self, other):
        if isinstance(other, CandleBuffer):
            return self.columns == other.columns
        return isinstance(other, Sequence) and len(self) == len(other) and all(a == tuple(b) for a, b in zip(self, other))


class ValueView(Sequence[PriceValues]):
    """绘图 OHLCV 视图，底层仍然是 CandleBuffer 的同一组列。"""
    def __init__(self, rows):
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    @overload
    def __getitem__(self, index: int) -> PriceValues: ...

    @overload
    def __getitem__(self, index: slice) -> list[PriceValues]: ...

    def __getitem__(self, index: int | slice) -> PriceValues | list[PriceValues]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        return cast(PriceValues, tuple(column[index] for column in self.rows.columns[1:6]))


class CandleIndex(Mapping[int, CandleRow]):
    def __init__(self, rows):
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows.times)

    def __getitem__(self, stamp):
        index = bisect_left(self.rows.times, stamp)
        if index == len(self.rows) or self.rows.times[index] != stamp:
            raise KeyError(stamp)
        return self.rows[index]


def candles(rows):
    if isinstance(rows, CandleBuffer):
        return rows
    clean = {}
    for row in rows:
        value = candle(row)
        previous = clean.get(value[0])
        if previous is None or value[8] >= previous[8]:
            clean[value[0]] = value
    return CandleBuffer(clean[stamp] for stamp in sorted(clean))
