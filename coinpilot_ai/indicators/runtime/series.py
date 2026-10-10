"""连续数值输出缓冲区，未预热以 NaN 保存、读取为 None。"""
from array import array
from collections.abc import Sequence
from math import isnan
from typing import overload


class NumericLine(Sequence[float | None]):
    """双精度连续存储，以 NaN 表示未预热，读取仍暴露 None。"""
    def __init__(self) -> None:
        self.data = array("d")

    def __len__(self) -> int:
        return len(self.data)

    @overload
    def __getitem__(self, index: int) -> float | None: ...

    @overload
    def __getitem__(self, index: slice) -> list[float | None]: ...

    def __getitem__(self, index: int | slice) -> float | None | list[float | None]:
        if isinstance(index, slice):
            return [None if isnan(value) else value for value in self.data[index]]
        value = self.data[index]
        return None if isnan(value) else value

    def __delitem__(self, index: int | slice) -> None:
        del self.data[index]

    def append(self, value: float | None) -> None:
        self.data.append(float("nan") if value is None else value)
