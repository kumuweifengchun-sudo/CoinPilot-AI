from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Diagnostic:
    severity: Literal["error", "warning", "info"]
    line: int
    column: int
    code: str
    message: str


class ScriptError(ValueError):
    def __init__(self, line: int, column: int, code: str, message: str) -> None:
        self.diagnostic = Diagnostic("error", line, column, code, message)
        super().__init__(f"第 {line} 行，第 {column} 列：{message}")
