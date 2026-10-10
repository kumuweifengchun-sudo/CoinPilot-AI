"""有限语法递归下降和 Pratt 表达式解析；从不调用 eval/exec。"""
from . import ast
from .diagnostics import ScriptError
from .lexer import Token, tokenize

PRECEDENCE = {"or": 1, "and": 2, "==": 3, "!=": 3, ">": 3, "<": 3, ">=": 3, "<=": 3,
              "+": 4, "-": 4, "*": 5, "/": 5, "%": 5}


class Parser:
    def __init__(self, source: str) -> None:
        self.tokens = tokenize(source)
        self.index = 0
        self.nodes = 0
        self.depth = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def take(self, text: str | None = None) -> Token:
        token = self.current
        if text is not None and token.text != text:
            raise ScriptError(token.line, token.column, "SYNTAX", "此处需要 " + text)
        self.index += 1
        return token

    def count(self) -> None:
        self.nodes += 1
        if self.nodes > 10000:
            token = self.current
            raise ScriptError(token.line, token.column, "AST_LIMIT", "AST 不能超过 10000 节点")

    def expression(self, minimum: int = 0) -> ast.Expression:
        self.depth += 1
        if self.depth > 100:
            token = self.current
            raise ScriptError(token.line, token.column, "NESTING_LIMIT", "表达式嵌套不能超过 100 层")
        self.count()
        token = self.take()
        left: ast.Expression
        if token.text in ("-", "+", "not"):
            left = ast.Unary(token, token.text, self.expression(3 if token.text == "not" else 6))
        elif token.text == "(":
            left = self.expression()
            self.take(")")
        elif token.kind == "number":
            left = ast.Literal(token, float(token.text) if any(char in token.text for char in ".eE") else int(token.text))
        elif token.kind == "string":
            left = ast.Literal(token, token.text)
        elif token.kind == "name":
            if token.text in ("true", "false", "na"):
                left = ast.Literal(token, {"true": True, "false": False, "na": None}[token.text])
            elif self.current.text == "(":
                self.take("(")
                args: list[ast.Expression] = []
                keywords: list[tuple[str, ast.Expression]] = []
                while self.current.text != ")":
                    if self.current.kind == "name" and self.tokens[self.index + 1].text == "=":
                        name = self.take().text
                        self.take("=")
                        keywords.append((name, self.expression()))
                    else:
                        if keywords:
                            raise ScriptError(self.current.line, self.current.column, "ARGUMENT", "命名参数后不能再使用位置参数")
                        args.append(self.expression())
                    if self.current.text != ",":
                        break
                    self.take(",")
                self.take(")")
                left = ast.Call(token, token.text, tuple(args), tuple(keywords))
            else:
                left = ast.Name(token, token.text)
        else:
            raise ScriptError(token.line, token.column, "EXPRESSION", "此处需要表达式")
        while self.current.text == "[":
            marker = self.take("[")
            offset = self.expression()
            self.take("]")
            left = ast.History(marker, left, offset)
            self.count()
        while self.current.text in PRECEDENCE and PRECEDENCE[self.current.text] >= minimum:
            marker = self.take()
            right = self.expression(PRECEDENCE[marker.text] + 1)
            left = ast.Binary(marker, marker.text, left, right)
            self.count()
        if minimum == 0 and self.current.text == "?":
            marker = self.take("?")
            yes = self.expression()
            self.take(":")
            left = ast.Conditional(marker, left, yes, self.expression())
            self.count()
        self.depth -= 1
        return left

    def block(self) -> tuple[ast.Statement, ...]:
        if self.depth >= 50:
            raise ScriptError(self.current.line, self.current.column, "NESTING_LIMIT", "分支嵌套不能超过 50 层")
        self.depth += 1
        if self.current.kind != "newline":
            raise ScriptError(self.current.line, self.current.column, "SYNTAX", "条件后需要换行和缩进")
        self.take()
        if self.current.kind != "indent":
            raise ScriptError(self.current.line, self.current.column, "INDENTATION", "条件分支需要缩进")
        self.take()
        statements: list[ast.Statement] = []
        while self.current.kind not in ("dedent", "eof"):
            statements.append(self.statement())
        if self.current.kind != "dedent":
            raise ScriptError(self.current.line, self.current.column, "INDENTATION", "条件分支未结束")
        self.take()
        self.depth -= 1
        return tuple(statements)

    def statement(self) -> ast.Statement:
        self.count()
        token = self.current
        if token.text in ("for", "while", "import", "export", "type", "method", "switch"):
            raise ScriptError(token.line, token.column, "UNSUPPORTED_SYNTAX", "v1 暂不支持 " + token.text)
        if token.text == "if":
            self.take()
            test = self.expression()
            yes = self.block()
            no: tuple[ast.Statement, ...] = ()
            if self.current.text == "else":
                self.take()
                no = (self.statement(),) if self.current.text == "if" else self.block()
            return ast.IfStatement(token, test, yes, no)
        # 单表达式函数，编译时内联；递归和无限展开由编译器拒绝。
        boundary = self.index
        while self.tokens[boundary].kind not in ("newline", "eof"):
            boundary += 1
        if any(item.text == "=>" for item in self.tokens[self.index:boundary]):
            name = self.take().text
            self.take("(")
            parameters: list[str] = []
            while self.current.text != ")":
                parameter = self.take()
                if parameter.kind != "name" or "." in parameter.text:
                    raise ScriptError(parameter.line, parameter.column, "FUNCTION", "函数参数名无效")
                parameters.append(parameter.text)
                if self.current.text != ",":
                    break
                self.take(",")
            self.take(")")
            self.take("=>")
            result = ast.FunctionStatement(token, name, tuple(parameters), self.expression())
        else:
            persistent = token.text == "var"
            if persistent:
                self.take()
            if self.current.text in ("float", "int", "bool"):
                self.take()
            target_index = self.index
            targets: list[str] = []
            if self.current.text == "[":
                self.take("[")
                while self.current.text != "]":
                    targets.append(self.take().text)
                    if self.current.text != ",":
                        break
                    self.take(",")
                self.take("]")
            elif self.current.kind == "name":
                targets.append(self.take().text)
            if targets and self.current.text in ("=", ":="):
                update = self.take().text == ":="
                result = ast.Assignment(token, tuple(targets), self.expression(), persistent, update)
            else:
                if persistent:
                    raise ScriptError(token.line, token.column, "VAR", "var 需要初始化赋值")
                self.index = target_index
                result = ast.ExpressionStatement(token, self.expression())
        if self.current.kind != "newline":
            raise ScriptError(self.current.line, self.current.column, "SYNTAX", "表达式后存在不支持的语法")
        self.take()
        return result

    def parse(self) -> tuple[ast.Statement, ...]:
        statements: list[ast.Statement] = []
        while self.current.kind != "eof":
            if self.current.kind == "newline":
                self.take()
            else:
                statements.append(self.statement())
        return tuple(statements)


def parse(source: str) -> tuple[ast.Statement, ...]:
    return Parser(source).parse()
