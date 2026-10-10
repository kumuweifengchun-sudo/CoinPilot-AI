from dataclasses import dataclass

from .lexer import Token


@dataclass(frozen=True)
class Literal:
    token: Token
    value: int | float | bool | str | None


@dataclass(frozen=True)
class Name:
    token: Token
    name: str


@dataclass(frozen=True)
class Unary:
    token: Token
    op: str
    value: "Expression"


@dataclass(frozen=True)
class Binary:
    token: Token
    op: str
    left: "Expression"
    right: "Expression"


@dataclass(frozen=True)
class History:
    token: Token
    value: "Expression"
    offset: "Expression"


@dataclass(frozen=True)
class Call:
    token: Token
    name: str
    args: tuple["Expression", ...]
    keywords: tuple[tuple[str, "Expression"], ...]


@dataclass(frozen=True)
class Conditional:
    token: Token
    test: "Expression"
    yes: "Expression"
    no: "Expression"


type Expression = Literal | Name | Unary | Binary | History | Call | Conditional


@dataclass(frozen=True)
class Assignment:
    token: Token
    targets: tuple[str, ...]
    value: Expression
    persistent: bool = False
    update: bool = False


@dataclass(frozen=True)
class ExpressionStatement:
    token: Token
    value: Expression


@dataclass(frozen=True)
class IfStatement:
    token: Token
    test: Expression
    yes: tuple["Statement", ...]
    no: tuple["Statement", ...]


@dataclass(frozen=True)
class FunctionStatement:
    token: Token
    name: str
    parameters: tuple[str, ...]
    value: Expression


type Statement = Assignment | ExpressionStatement | IfStatement | FunctionStatement
