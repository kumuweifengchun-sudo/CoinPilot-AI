"""线性扫描、确定缩进；限制输入规模后才构造 token。"""
from dataclasses import dataclass
import math
import re
from typing import Literal

from .diagnostics import ScriptError

MAX_SOURCE_BYTES = 256 * 1024


@dataclass(frozen=True)
class Token:
    kind: Literal["name", "number", "string", "op", "newline", "indent", "dedent", "eof"]
    text: str
    line: int
    column: int


def tokenize(source: str) -> tuple[Token, ...]:
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ScriptError(1, 1, "SOURCE_LIMIT", "源码不能超过 256KiB")
    tokens: list[Token] = []
    indents = [0]
    depth = 0
    for line_number, line in enumerate(source.splitlines(), 1):
        offset = len(line) - len(line.lstrip(" "))
        if "\t" in line[:offset + 1]:
            raise ScriptError(line_number, 1, "INDENTATION", "缩进请使用空格")
        text = line[offset:]
        if not text or text.startswith("//"):
            continue
        if depth == 0:
            if offset > indents[-1]:
                indents.append(offset)
                tokens.append(Token("indent", "", line_number, 1))
            while offset < indents[-1]:
                indents.pop()
                tokens.append(Token("dedent", "", line_number, 1))
            if offset != indents[-1]:
                raise ScriptError(line_number, 1, "INDENTATION", "缩进层级不一致")
        index = offset
        while index < len(line):
            char = line[index]
            if char.isspace():
                index += 1
                continue
            if line[index:index + 2] == "//":
                break
            column = index + 1
            if char in ("\"", "'"):
                quote = char
                index += 1
                value = ""
                while index < len(line) and line[index] != quote:
                    if line[index] == "\\":
                        index += 1
                        if index >= len(line):
                            break
                        value += {"n": "\n", "t": "\t", "r": "\r"}.get(line[index], line[index])
                    else:
                        value += line[index]
                    index += 1
                if index >= len(line):
                    raise ScriptError(line_number, column, "STRING", "字符串未结束")
                tokens.append(Token("string", value, line_number, column))
                index += 1
            elif char.isdigit() or char == "." and index + 1 < len(line) and line[index + 1].isdigit():
                match = re.match(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", line[index:])
                if match is None or not math.isfinite(float(match[0])):
                    raise ScriptError(line_number, column, "NUMBER", "数值无效或过大")
                tokens.append(Token("number", match[0], line_number, column))
                index += len(match[0])
            elif char.isalpha() or char == "_":
                match = re.match(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", line[index:])
                if match is None:
                    raise ScriptError(line_number, column, "NAME", "变量名请使用英文、数字和下划线")
                tokens.append(Token("name", match[0], line_number, column))
                index += len(match[0])
            else:
                pair = line[index:index + 2]
                operator = pair if pair in (">=", "<=", "==", "!=", ":=", "=>") else char
                if operator not in ("+", "-", "*", "/", "%", ">", "<", ">=", "<=", "==", "!=",
                                    "=", ":=", "=>", "(", ")", "[", "]", ",", "?", ":"):
                    raise ScriptError(line_number, column, "TOKEN", "不支持的语法字符")
                tokens.append(Token("op", operator, line_number, column))
                index += len(operator)
                if operator in ("(", "["):
                    depth += 1
                    if depth > 100:
                        raise ScriptError(line_number, column, "NESTING_LIMIT", "表达式嵌套不能超过 100 层")
                elif operator in (")", "]"):
                    depth -= 1
                    if depth < 0:
                        raise ScriptError(line_number, column, "BRACKET", "括号不匹配")
            if len(tokens) > 40000:
                raise ScriptError(line_number, column, "AST_LIMIT", "源码结构超过资源限制")
        if depth == 0:
            tokens.append(Token("newline", "", line_number, len(line) + 1))
    if depth:
        raise ScriptError(len(source.splitlines()), 1, "BRACKET", "括号未结束")
    while len(indents) > 1:
        indents.pop()
        tokens.append(Token("dedent", "", len(source.splitlines()) + 1, 1))
    tokens.append(Token("eof", "", len(source.splitlines()) + 1, 1))
    return tuple(tokens)
