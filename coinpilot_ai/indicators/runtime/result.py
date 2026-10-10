"""运行结果只提供有类型的数值，不包含图表绘制规则。"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..models import OutputKind

type SeriesValue = float | int | bool | None


@dataclass(frozen=True)
class IndicatorResultMetadata:
    output_types: Mapping[str, OutputKind] = field(default_factory=dict)
    timeframe: str | None = None


@dataclass(frozen=True)
class IndicatorResult:
    # 四个旧参数仍可构造结果；实际运行用 definition_id/revision 标识。
    kind: str
    times: Sequence[int]
    lines: Mapping[str, Sequence[float | None]]
    confirmed: Sequence[bool | int]
    definition_id: str = ""
    revision: int = 1
    metadata: IndicatorResultMetadata = field(default_factory=IndicatorResultMetadata)

    def __post_init__(self) -> None:
        if not self.definition_id:
            object.__setattr__(self, "definition_id", "builtin." + self.kind.lower())

    @property
    def outputs(self) -> Mapping[str, Sequence[float | None]]:
        return self.lines

    @property
    def valid(self) -> dict[str, tuple[bool, ...]]:
        return {key: tuple(value is not None for value in values) for key, values in self.lines.items()}

    @property
    def warming(self) -> dict[str, tuple[bool, ...]]:
        return {key: tuple(value is None for value in values) for key, values in self.lines.items()}

    def latest(self, line: str = "value", *, closed: bool = True) -> tuple[int, float] | None:
        values = self.lines[line]
        for index in range(len(values) - 1, -1, -1):
            value = values[index]
            if value is not None and (not closed or self.confirmed[index]):
                return self.times[index], value
        return None
