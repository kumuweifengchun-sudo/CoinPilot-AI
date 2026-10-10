"""封闭操作集的数据流图；历史节点只读上一个 bar 及更早的值。"""
from dataclasses import dataclass

from ..models import IndicatorDefinition, InputValue, OutputKind

type Constant = InputValue | None


@dataclass(frozen=True)
class Instruction:
    op: str
    args: tuple[int, ...] = ()
    value: Constant = None
    type: OutputKind | str = "float"
    line: int = 1
    column: int = 1


@dataclass(frozen=True)
class Program:
    definition: IndicatorDefinition
    instructions: tuple[Instruction, ...]
    outputs: tuple[tuple[str, int], ...]
    history_limits: tuple[int, ...]
    source_hash: str
    compiler_version: int = 1
