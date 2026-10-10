"""指标定义与图表实例分离，所有外部记录须经过 validation。"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

type InputValue = int | float | bool | str
type InputKind = Literal["int", "float", "bool", "source"]
type OutputKind = Literal["float", "bool", "int"]
type PlotKind = Literal["line", "histogram", "shape", "horizontal_line", "background"]
type PlotTarget = Literal["overlay", "pane"]
type SourceKind = Literal["builtin", "coinpilot", "pine", "ai"]


@dataclass(frozen=True)
class InputDefinition:
    id: str
    title: str
    type: InputKind
    default: InputValue
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


@dataclass(frozen=True)
class OutputDefinition:
    id: str
    title: str
    type: OutputKind = "float"


@dataclass(frozen=True)
class PlotDefinition:
    id: str
    title: str
    output: str
    type: PlotKind = "line"
    target: PlotTarget = "overlay"
    color: str = "#35a4ff"
    value: float | None = None
    width: float = 1.5
    marker: Literal["triangle_up", "triangle_down", "circle", "cross"] = "triangle_up"
    placement: Literal["top", "bottom"] = "bottom"


@dataclass(frozen=True)
class ScaleDefinition:
    mode: Literal["auto", "fixed", "percentage", "price"] = "auto"
    minimum: float | None = None
    maximum: float | None = None
    levels: tuple[float, ...] = ()


@dataclass(frozen=True)
class PaneDefinition:
    mode: PlotTarget = "overlay"
    scale: ScaleDefinition = ScaleDefinition()
    height: int = 112


@dataclass(frozen=True)
class IndicatorDefinition:
    id: str
    name: str
    title: str
    source: SourceKind
    revision: int
    inputs: tuple[InputDefinition, ...]
    outputs: tuple[OutputDefinition, ...]
    plots: tuple[PlotDefinition, ...]
    pane: PaneDefinition = PaneDefinition()
    # 跨输入约束由定义声明，消费者不识别指标名。
    ordered_inputs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PlotStyle:
    visible: bool = True
    color: str = "#35a4ff"
    width: float = 1.5


@dataclass(frozen=True)
class IndicatorInstance:
    id: str
    definition: str
    definition_revision: int
    inputs: Mapping[str, InputValue] = field(default_factory=dict)
    timeframe: str = "chart"
    plots: Mapping[str, PlotStyle] = field(default_factory=dict)
    visible: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))
        object.__setattr__(self, "plots", MappingProxyType(dict(self.plots)))


@dataclass(frozen=True)
class IndicatorCacheKey:
    definition_id: str
    definition_revision: int
    inputs: tuple[tuple[str, InputValue], ...]
    timeframe: str

    @classmethod
    def of(cls, instance: IndicatorInstance, timeframe: str) -> "IndicatorCacheKey":
        return cls(instance.definition, instance.definition_revision,
                   tuple(sorted(instance.inputs.items())), timeframe)
