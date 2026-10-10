"""编译有限语法至 IR；危险 API 和未知调用在执行前拒绝。"""
from dataclasses import replace
import hashlib
import math
import re

from ..models import (IndicatorDefinition, InputDefinition, OutputDefinition,
                      PaneDefinition, PlotDefinition, PlotTarget, ScaleDefinition, SourceKind)
from . import ast
from .diagnostics import Diagnostic, ScriptError
from .ir import Constant, Instruction, Program
from .lexer import Token
from .parser import parse

SOURCES = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "barstate.isconfirmed", "bar_index"}
TA_ARITY = {"ta.sma": 2, "ta.ema": 2, "ta.rma": 2, "ta.rsi": 2, "ta.atr": 1, "ta.macd": 4,
            "ta.highest": 2, "ta.lowest": 2, "ta.stdev": 2, "ta.bb": 3, "ta.change": 2,
            "ta.cross": 2, "ta.crossover": 2, "ta.crossunder": 2, "ta.tr": 0}
MATH_ARITY = {"math.abs": 1, "math.sqrt": 1, "math.round": 1, "math.max": 2, "math.min": 2}
COLORS = {"color.green": "#22c55e", "color.red": "#ef4444", "color.blue": "#35a4ff",
          "color.orange": "#f59e0b", "color.white": "#ffffff", "color.yellow": "#facc15",
          "color.gray": "#888888", "color.black": "#000000", "color.aqua": "#00ffff",
          "color.purple": "#a855f7", "color.lime": "#00ff00", "color.fuchsia": "#ff00ff"}


class Compiler:
    def __init__(self, source: str, identity: str, revision: int, language: SourceKind) -> None:
        self.source, self.identity, self.revision = source, identity, revision
        self.language: SourceKind = language
        self.nodes: list[Instruction] = []
        self.variables: dict[str, int] = {}
        self.constants: dict[str, Constant] = {}
        self.inputs: list[InputDefinition] = []
        self.plots: list[PlotDefinition] = []
        self.outputs: list[tuple[str, int]] = []
        self.functions: dict[str, ast.FunctionStatement] = {}
        self.active_functions: set[str] = set()
        self.persistent: dict[str, int] = {}
        self.late_history: list[tuple[int, str]] = []
        self.history_limits: dict[int, int] = {}
        self.title = "自定义指标"
        self.target: PlotTarget = "pane"
        self.declared = False
        self.depth = 0
        self.branch_depth = 0

    def error(self, token: Token, code: str, message: str) -> ScriptError:
        return ScriptError(token.line, token.column, code, message)

    def emit(self, op: str, args: tuple[int, ...], token: Token, *, value: Constant = None,
             kind: str = "float") -> int:
        if len(self.nodes) >= 2000:
            raise self.error(token, "NODE_LIMIT", "执行图不能超过 2000 节点")
        self.nodes.append(Instruction(op, args, value, kind, token.line, token.column))
        return len(self.nodes) - 1

    def constant(self, value: ast.Expression) -> Constant:
        if isinstance(value, ast.Literal):
            return value.value
        if isinstance(value, ast.Name):
            if value.name in COLORS:
                return COLORS[value.name]
            if value.name in self.variables:
                return self.node_constant(self.variables[value.name])
            if value.name in self.constants:
                return self.constants[value.name]
            if value.name.startswith(("plot.style_", "shape.", "location.")):
                return value.name
        if isinstance(value, ast.Unary):
            operand = self.constant(value.value)
            if value.op == "not" and isinstance(operand, bool):
                return not operand
            if not isinstance(operand, bool) and isinstance(operand, (int, float)):
                return -operand if value.op == "-" else operand
        raise self.error(value.token, "CONSTANT", "此参数需要常量或 input 值")

    def node_constant(self, ref: int, *, allow_input: bool = True, depth: int = 0) -> Constant:
        node = self.nodes[ref]
        def invalid() -> ScriptError:
            return ScriptError(node.line, node.column, "CONSTANT", "此参数需要常量或直接的 input 值")
        if depth > 100:
            raise invalid()
        if node.op == "constant":
            return node.value
        if node.op == "input" and allow_input:
            return next(item.default for item in self.inputs if item.id == node.value)
        # 不把输入参与运算后的默认值冒充编译期常量。
        values = [self.node_constant(arg, allow_input=False, depth=depth+1) for arg in node.args]
        if node.op == "not" and len(values) == 1 and isinstance(values[0], bool):
            return not values[0]
        if node.op not in ("+", "-", "*", "/", "%"):
            raise invalid()
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise invalid()
        numbers = [value for value in values if isinstance(value, (int, float))]
        if len(numbers) == 1:
            return -numbers[0] if node.op == "-" else numbers[0]
        if len(numbers) != 2:
            raise invalid()
        a, b = numbers
        result = a+b if node.op == "+" else a-b if node.op == "-" else a*b if node.op == "*" else a/b if node.op == "/" and b else a%b if node.op == "%" and b else None
        if result is None or not math.isfinite(result):
            raise invalid()
        return result

    def keywords(self, call: ast.Call, allowed: set[str]) -> dict[str, ast.Expression]:
        values = dict(call.keywords)
        if len(values) != len(call.keywords) or set(values) - allowed:
            raise self.error(call.token, "ARGUMENT", "命名参数重复或不受 v1 支持")
        return values

    def source_node(self, name: str, token: Token) -> int:
        return self.emit("source", (), token, value=name,
                         kind="bool" if name == "barstate.isconfirmed" else "int" if name == "bar_index" else "float")

    def bound_input(self, ref: int, minimum: int, maximum: int) -> bool:
        node = self.nodes[ref]
        if node.op != "input":
            return False
        for index, definition in enumerate(self.inputs):
            if definition.id == node.value:
                self.inputs[index] = replace(definition,
                    minimum=max(minimum, definition.minimum if definition.minimum is not None else minimum),
                    maximum=min(maximum, definition.maximum if definition.maximum is not None else maximum))
                return True
        return False

    def expression(self, value: ast.Expression) -> int:
        self.depth += 1
        if self.depth > 100:
            raise self.error(value.token, "NESTING_LIMIT", "编译表达式嵌套不能超过 100 层")
        try:
            return self._expression(value)
        finally:
            self.depth -= 1

    def _expression(self, value: ast.Expression) -> int:
        token = value.token
        if isinstance(value, ast.Literal):
            kind = "na" if value.value is None else "bool" if isinstance(value.value, bool) else "string" if isinstance(value.value, str) else "float"
            return self.emit("constant", (), token, value=value.value, kind=kind)
        if isinstance(value, ast.Name):
            if value.name in self.variables:
                return self.variables[value.name]
            if value.name in SOURCES:
                return self.source_node(value.name, token)
            if value.name == "ta.tr":
                return self.emit("ta.tr", (), token)
            if value.name in COLORS:
                return self.emit("constant", (), token, value=COLORS[value.name], kind="string")
            raise self.error(token, "UNKNOWN_NAME", "未知变量或不支持的内置字段：" + value.name)
        if isinstance(value, ast.Unary):
            ref = self.expression(value.value)
            expected = ("bool", "na") if value.op == "not" else ("float", "int", "na")
            if self.nodes[ref].type not in expected:
                raise self.error(token, "TYPE", "一元运算输入类型不匹配")
            return self.emit(value.op, (ref,), token, kind="bool" if value.op == "not" else "float")
        if isinstance(value, ast.Binary):
            args = self.expression(value.left), self.expression(value.right)
            if any(self.nodes[ref].type in ("string", "tuple") for ref in args):
                raise self.error(token, "TYPE", "字符串和元组不能参与数值或布尔运算")
            if value.op in ("and", "or") and any(self.nodes[ref].type not in ("bool", "na") for ref in args):
                raise self.error(token, "TYPE", "逻辑运算需要布尔输入")
            if value.op not in ("and", "or", "==", "!=") and any(self.nodes[ref].type == "bool" for ref in args):
                raise self.error(token, "TYPE", "数值运算不能使用布尔输入")
            return self.emit(value.op, args, token, kind="bool" if value.op in ("and", "or", ">", "<", ">=", "<=", "==", "!=") else "float")
        if isinstance(value, ast.Conditional):
            args = self.expression(value.test), self.expression(value.yes), self.expression(value.no)
            if self.nodes[args[0]].type not in ("bool", "na"):
                raise self.error(token, "TYPE", "条件表达式需要布尔输入")
            a, b = self.nodes[args[1]].type, self.nodes[args[2]].type
            if a != b and "na" not in (a, b) and not {a, b} <= {"float", "int"}:
                raise self.error(token, "TYPE", "条件两侧输出类型不匹配")
            return self.emit("select", args, token, kind=b if a == "na" else a)
        if isinstance(value, ast.History):
            offset = self.constant(value.offset)
            if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
                raise self.error(token, "HISTORY_LIMIT", "历史引用须为 0—10000 的整数，不允许未来引用")
            ref = self.expression(value.value)
            offset_ref = self.expression(value.offset)
            dynamic = self.bound_input(offset_ref, 0, 10000)
            if not dynamic:
                pending = [offset_ref]
                while pending:
                    dependency = self.nodes[pending.pop()]
                    if dependency.op == "input":
                        raise self.error(token, "HISTORY_LIMIT", "动态历史索引须直接使用 input.int，不支持索引运算")
                    pending.extend(dependency.args)
            limit = 10000 if dynamic else offset
            node = self.emit("history", (ref, offset_ref, ref) if dynamic else (ref,), token,
                             value=limit, kind=self.nodes[ref].type)
            if ((offset > 0 or dynamic) and isinstance(value.value, ast.Name)
                    and value.value.name in self.variables and not self.active_functions):
                self.late_history.append((node, value.value.name))
            else:
                self.history_limits[ref] = max(limit, self.history_limits.get(ref, 1))
            return node
        return self.call(value)

    def input(self, call: ast.Call, name: str) -> int:
        if self.branch_depth or len(self.inputs) >= 64:
            raise self.error(call.token, "INPUT_LIMIT", "input 须在顶层声明，最多 64 项")
        options = self.keywords(call, {"defval", "title", "minval", "maxval", "step"})
        if len(call.args) > 2 or not call.args and "defval" not in options:
            raise self.error(call.token, "ARGUMENT", "input 需要默认值，可选标题与范围")
        default_node = call.args[0] if call.args else options["defval"]
        default = default_node.name if call.name == "input.source" and isinstance(default_node, ast.Name) else self.constant(default_node)
        title = self.constant(call.args[1]) if len(call.args) > 1 else self.constant(options["title"]) if "title" in options else name
        if not isinstance(title, str) or not 1 <= len(title) <= 120:
            raise self.error(call.token, "TYPE", "input 标题须为字符串")
        input_type = call.name.removeprefix("input.")
        if input_type not in ("int", "float", "bool", "source"):
            raise self.error(call.token, "UNSUPPORTED_CALL", "v1 不支持该 input 类型")
        minimum = self.constant(options["minval"]) if "minval" in options else None
        maximum = self.constant(options["maxval"]) if "maxval" in options else None
        step = self.constant(options["step"]) if "step" in options else None
        if input_type in ("bool", "source") and any(key in options for key in ("minval", "maxval", "step")):
            raise self.error(call.token, "INPUT", "布尔与 source 输入不支持数值范围或步长")
        for bound in (minimum, maximum, step):
            if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(bound)):
                raise self.error(call.token, "INPUT", "输入范围和步长须为有限数值")
        if step is not None and (not isinstance(step, (int, float)) or step <= 0):
            raise self.error(call.token, "INPUT", "步长必须大于零")
        if input_type == "int" and any(bound is not None and (
                not isinstance(bound, int) or not -2147483647 <= bound <= 2147483647) for bound in (default, minimum, maximum, step)):
            raise self.error(call.token, "INPUT", "整数输入、范围和步长须在 32 位整数范围内")
        if input_type == "float" and any(isinstance(bound, (int, float)) and abs(bound) > 1e12 for bound in (default, minimum, maximum, step)):
            raise self.error(call.token, "INPUT", "浮点输入范围不能超过正负 1e12")
        if default is None or minimum is not None and not isinstance(minimum, (int, float)) or maximum is not None and not isinstance(maximum, (int, float)):
            raise self.error(call.token, "TYPE", "input 默认值或范围无效")
        kind = input_type
        if kind == "int":
            definition = InputDefinition(name, title, "int", default, minimum, maximum)
        elif kind == "float":
            definition = InputDefinition(name, title, "float", default, minimum, maximum)
        elif kind == "bool":
            definition = InputDefinition(name, title, "bool", default)
        else:
            definition = InputDefinition(name, title, "source", default)
        if isinstance(step, (int, float)):
            definition = replace(definition, step=float(step))
        from ..validation import input_value
        try:
            input_value(definition, default)
        except ValueError as exc:
            raise self.error(call.token, "INPUT", str(exc)) from None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise self.error(call.token, "INPUT", "input 范围下限不能超过上限")
        if any(item.id == name for item in self.inputs):
            raise self.error(call.token, "INPUT", "input 名称重复")
        self.inputs.append(definition)
        self.constants[name] = default
        return self.emit("input", (), call.token, value=name, kind="bool" if kind == "bool" else "float")

    def call(self, call: ast.Call) -> int:
        name = call.name
        if name in self.functions:
            function = self.functions[name]
            if name in self.active_functions:
                raise self.error(call.token, "RECURSION", "不支持递归函数")
            if call.keywords or len(call.args) != len(function.parameters):
                raise self.error(call.token, "ARGUMENT", "函数参数数量无效")
            if len(set(self.variables) | set(function.parameters)) > 512:
                raise self.error(call.token, "VARIABLE_LIMIT", "变量和函数参数合计不能超过 512 项")
            refs = tuple(self.expression(arg) for arg in call.args)
            old = self.variables.copy()
            old_constants = self.constants.copy()
            self.variables.update(zip(function.parameters, refs))
            for parameter, argument in zip(function.parameters, call.args):
                try:
                    self.constants[parameter] = self.constant(argument)
                except ScriptError:
                    self.constants.pop(parameter, None)
            self.active_functions.add(name)
            try:
                return self.expression(function.value)
            finally:
                self.active_functions.remove(name)
                self.variables = old
                self.constants = old_constants
        if name in TA_ARITY or name in MATH_ARITY:
            if call.keywords:
                raise self.error(call.token, "ARGUMENT", "TA 和数学函数 v1 使用位置参数")
            args = list(call.args)
            if name in ("ta.highest", "ta.lowest") and len(args) == 1:
                args.insert(0, ast.Name(call.token, "high" if name == "ta.highest" else "low"))
            if name == "ta.change" and len(args) == 1:
                args.append(ast.Literal(call.token, 1))
            if name == "ta.tr" and len(args) == 1 and self.constant(args[0]) is True:
                args = []
            required = TA_ARITY.get(name, MATH_ARITY.get(name, 0))
            if len(args) != required and not (name in ("math.max", "math.min") and 2 <= len(args) <= 64):
                raise self.error(call.token, "ARGUMENT", name + " 参数数量无效")
            refs = tuple(self.expression(arg) for arg in args)
            expected = ("float", "int", "na")
            if any(self.nodes[ref].type not in expected for ref in refs):
                raise self.error(call.token, "TYPE", "TA 和数学函数需要数值参数")
            periods = (0,) if name == "ta.atr" else (1, 2, 3) if name == "ta.macd" else (1,) if name in (
                "ta.sma", "ta.ema", "ta.rma", "ta.rsi", "ta.highest", "ta.lowest", "ta.stdev", "ta.bb", "ta.change") else ()
            for index in periods:
                period = self.constant(args[index])
                if isinstance(period, bool) or not isinstance(period, int) or not 1 <= period <= 10000:
                    raise self.error(call.token, "PERIOD", "TA 周期须为 1—10000 的整数常量或 input")
                self.bound_input(refs[index], 1, 10000)
            if name == "ta.change":
                length_node = self.nodes[refs[1]]
                length = self.constant(args[1])
                if not isinstance(length, int):
                    raise self.error(call.token, "PERIOD", "change 长度须为整数")
                capacity = 10000 if length_node.op == "input" else length
                self.history_limits[refs[0]] = max(capacity, self.history_limits.get(refs[0], 1))
            return self.emit(name, refs, call.token, kind="tuple" if name in ("ta.macd", "ta.bb") else
                             "bool" if name in ("ta.cross", "ta.crossover", "ta.crossunder") else "float")
        if name in ("na", "nz"):
            if call.keywords or len(call.args) not in ((1,) if name == "na" else (1, 2)):
                raise self.error(call.token, "ARGUMENT", name + " 参数数量无效")
            return self.emit(name, tuple(self.expression(arg) for arg in call.args), call.token,
                             kind="bool" if name == "na" else "float")
        raise self.error(call.token, "UNSUPPORTED_CALL", "v1 暂不支持调用 " + name)

    def declaration(self, call: ast.Call) -> None:
        if self.declared or self.branch_depth:
            raise self.error(call.token, "INDICATOR", "indicator 只能在顶层声明一次")
        options = self.keywords(call, {"title", "shorttitle", "overlay"})
        title = self.constant(call.args[0]) if call.args else self.constant(options["title"]) if "title" in options else None
        if not isinstance(title, str) or not title.strip() or len(title) > 120 or len(call.args) > 1:
            raise self.error(call.token, "INDICATOR", "indicator 需要有效的标题")
        overlay = self.constant(options["overlay"]) if "overlay" in options else False
        if not isinstance(overlay, bool):
            raise self.error(call.token, "INDICATOR", "overlay 须为布尔常量")
        self.title, self.target, self.declared = title, "overlay" if overlay else "pane", True

    def plot(self, call: ast.Call) -> None:
        if not self.declared or self.branch_depth or len(self.plots) >= 32:
            raise self.error(call.token, "PLOT_LIMIT", "绘图须在 indicator 后的顶层声明，最多 32 项")
        options = self.keywords(call, {"title", "color", "linewidth", "style", "location", "size"})
        if "location" in options and call.name != "plotshape" or "style" in options and call.name not in ("plot", "plotshape"):
            raise self.error(call.token, "PLOT_STYLE", "绘图类型不支持该 style 或 location 参数")
        if len(call.args) not in (1, 2):
            raise self.error(call.token, "ARGUMENT", "绘图需要一个输出表达式及可选标题")
        title = self.constant(options["title"]) if "title" in options else self.constant(call.args[1]) if len(call.args) == 2 else "输出 " + str(len(self.plots) + 1)
        shade = self.constant(options["color"]) if "color" in options else "#35a4ff"
        expression = call.args[0]
        if call.name == "bgcolor":
            if isinstance(expression, ast.Conditional):
                shade = self.constant(expression.yes)
                if self.constant(expression.no) is not None:
                    raise self.error(call.token, "BG_COLOR", "v1 bgcolor 使用 条件 ? 固定颜色 : na")
                expression = expression.test
            else:
                shade = self.constant(expression)
                expression = ast.Literal(call.token, True)
        if (not isinstance(title, str) or not 1 <= len(title) <= 120 or not isinstance(shade, str)
                or not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", shade)):
            raise self.error(call.token, "PLOT", "绘图标题或固定颜色无效")
        ref = self.expression(expression)
        if self.nodes[ref].type in ("tuple", "string"):
            raise self.error(call.token, "TYPE", "绘图需要数值或布尔输出")
        if call.name in ("plotshape", "bgcolor") and self.nodes[ref].type not in ("bool", "na"):
            raise self.error(call.token, "TYPE", "v1 标记和背景需要布尔条件")
        identity = "plot_" + str(len(self.plots) + 1)
        output_id = expression.name if isinstance(expression, ast.Name) and "." not in expression.name else identity
        existing = dict(self.outputs).get(output_id)
        if existing is not None and existing != ref:
            output_id = identity
        style = self.constant(options["style"]) if "style" in options else None
        if call.name == "plot" and style not in (None, "plot.style_line", "plot.style_histogram", "plot.style_columns"):
            raise self.error(call.token, "PLOT_STYLE", "v1 不支持该绘图样式")
        kind = "shape" if call.name == "plotshape" else "horizontal_line" if call.name == "hline" else "background" if call.name == "bgcolor" else "line"
        if style in ("plot.style_histogram", "plot.style_columns"):
            kind = "histogram"
        if kind == "shape":
            definition = PlotDefinition(identity, title, output_id, "shape", self.target, shade)
        elif kind == "horizontal_line":
            horizontal = self.constant(expression)
            if isinstance(horizontal, bool) or not isinstance(horizontal, (int, float)):
                raise self.error(call.token, "HLINE", "水平线需要数值常量")
            definition = PlotDefinition(identity, title, output_id, "horizontal_line", self.target, shade, float(horizontal))
        elif kind == "background":
            definition = PlotDefinition(identity, title, output_id, "background", self.target, shade)
        else:
            definition = PlotDefinition(identity, title, output_id, "histogram" if kind == "histogram" else "line", self.target, shade)
        width = self.constant(options["linewidth"]) if "linewidth" in options else 1.5
        if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or not .5 <= width <= 6:
            raise self.error(call.token, "PLOT_STYLE", "线宽须为 0.5—6")
        definition = replace(definition, width=float(width))
        if kind == "shape":
            marker = style or "shape.triangleup"
            if marker == "shape.triangleup":
                definition = replace(definition, marker="triangle_up")
            elif marker == "shape.triangledown":
                definition = replace(definition, marker="triangle_down")
            elif marker == "shape.circle":
                definition = replace(definition, marker="circle")
            elif marker == "shape.cross":
                definition = replace(definition, marker="cross")
            else:
                raise self.error(call.token, "PLOT_STYLE", "v1 不支持该标记样式")
            location = self.constant(options["location"]) if "location" in options else "location.belowbar"
            if location in ("location.abovebar", "location.top"):
                definition = replace(definition, placement="top")
            elif location not in ("location.belowbar", "location.bottom"):
                raise self.error(call.token, "PLOT_STYLE", "v1 不支持该标记位置")
        if "size" in options:
            raise self.error(call.token, "PLOT_STYLE", "v1 标记采用固定尺寸，暂不支持 size")
        self.plots.append(definition)
        if output_id not in dict(self.outputs):
            self.outputs.append((output_id, ref))

    def statements(self, statements: tuple[ast.Statement, ...]) -> None:
        for statement in statements:
            if isinstance(statement, ast.FunctionStatement):
                continue
            if isinstance(statement, ast.IfStatement):
                test = self.expression(statement.test)
                if self.nodes[test].type not in ("bool", "na"):
                    raise self.error(statement.token, "TYPE", "if 条件需要布尔值")
                original = self.variables.copy()
                self.branch_depth += 1
                if self.branch_depth > 50:
                    raise self.error(statement.token, "NESTING_LIMIT", "分支嵌套不能超过 50 层")
                self.statements(statement.yes)
                yes = self.variables.copy()
                self.variables = original.copy()
                self.statements(statement.no)
                no = self.variables.copy()
                self.branch_depth -= 1
                self.variables = original.copy()
                for name in set(yes) | set(no):
                    a, b = yes.get(name), no.get(name)
                    if a is None or b is None:
                        raise self.error(statement.token, "BRANCH_VARIABLE", "分支中的新变量需要在两侧声明")
                    if a != b:
                        self.variables[name] = self.emit("select", (test, a, b), statement.token, kind=self.nodes[a].type)
            elif isinstance(statement, ast.Assignment):
                if len(set(self.variables) | set(statement.targets)) > 512:
                    raise self.error(statement.token, "VARIABLE_LIMIT", "变量不能超过 512 项")
                if any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) or len(name) > 128 or name in SOURCES for name in statement.targets):
                    raise self.error(statement.token, "VARIABLE", "变量名无效或覆盖行情字段")
                if statement.update and any(name not in self.variables for name in statement.targets):
                    raise self.error(statement.token, "VARIABLE", "重新赋值需要已有变量")
                if not statement.update and any(name in self.variables for name in statement.targets):
                    raise self.error(statement.token, "VARIABLE", "已有变量请使用 := 重新赋值")
                if isinstance(statement.value, ast.Call) and statement.value.name.startswith("input."):
                    if len(statement.targets) != 1:
                        raise self.error(statement.token, "INPUT", "input 需要单个变量")
                    ref = self.input(statement.value, statement.targets[0])
                else:
                    ref = self.expression(statement.value)
                if len(statement.targets) > 1:
                    length = 3 if self.nodes[ref].op in ("ta.macd", "ta.bb") else 0
                    if len(statement.targets) != length:
                        raise self.error(statement.token, "TUPLE", "多变量赋值与函数输出数量不一致")
                    for index, name in enumerate(statement.targets):
                        self.variables[name] = self.emit("extract", (ref,), statement.token, value=index)
                elif len(statement.targets) == 1:
                    name = statement.targets[0]
                    if statement.persistent:
                        if self.branch_depth:
                            raise self.error(statement.token, "VAR", "var 须在顶层初始化")
                        ref = self.emit("var", (ref,), statement.token, kind=self.nodes[ref].type)
                        self.persistent[name] = ref
                    self.variables[name] = ref
                    try:
                        self.constants[name] = self.constant(statement.value)
                    except ScriptError:
                        if name not in {item.id for item in self.inputs}:
                            self.constants.pop(name, None)
                else:
                    raise self.error(statement.token, "VARIABLE", "赋值缺少目标变量")
            else:
                value = statement.value
                if isinstance(value, ast.Call) and value.name == "indicator":
                    self.declaration(value)
                elif isinstance(value, ast.Call) and value.name in ("plot", "plotshape", "hline", "bgcolor"):
                    self.plot(value)
                else:
                    self.expression(value)

    def compile(self) -> Program:
        versions = re.findall(r"(?m)^\s*//@version=(\d+)\s*$", self.source)
        if len(versions) > 1 or versions and versions[0] not in ("5", "6"):
            raise ScriptError(1, 1, "PINE_VERSION", "v1 仅支持 Pine v5/v6 基础指标语法")
        statements = parse(self.source)
        for statement in statements:
            if isinstance(statement, ast.FunctionStatement):
                if statement.name in self.functions or len(set(statement.parameters)) != len(statement.parameters):
                    raise self.error(statement.token, "FUNCTION", "函数或参数名称重复")
                self.functions[statement.name] = statement
        self.validate_functions()
        self.statements(statements)
        if not self.declared or not self.plots:
            raise ScriptError(1, 1, "INDICATOR", "脚本需要 indicator 声明和至少一个绘图输出")
        for index, name in self.late_history:
            ref = self.variables[name]
            node = self.nodes[index]
            self.nodes[index] = replace(node, args=(*node.args[:2], ref) if len(node.args) == 3 else (ref,),
                                        type=self.nodes[ref].type)
            offset = node.value
            if isinstance(offset, int):
                self.history_limits[ref] = max(offset, self.history_limits.get(ref, 1))
        marker = Token("name", "", 1, 1)
        for name, initial in self.persistent.items():
            self.emit("commit", (initial, self.variables[name]), marker)
        outputs = tuple(OutputDefinition(key, next(plot.title for plot in self.plots if plot.output == key),
                                        "bool" if self.nodes[ref].type == "bool" else "float")
                        for key, ref in self.outputs)
        definition = IndicatorDefinition(self.identity, self.title, self.title, self.language, self.revision,
            tuple(self.inputs), outputs, tuple(self.plots), PaneDefinition(self.target, ScaleDefinition()))
        return Program(definition, tuple(self.nodes), tuple(self.outputs),
                       tuple(max(1, self.history_limits.get(index, 1)) for index in range(len(self.nodes))),
                       hashlib.sha256(self.source.encode("utf-8")).hexdigest())

    def validate_functions(self) -> None:
        graph: dict[str, set[str]] = {}
        for name, function in self.functions.items():
            graph[name] = set()
            pending: list[ast.Expression] = [function.value]
            while pending:
                value = pending.pop()
                if isinstance(value, ast.Call):
                    if value.name in self.functions:
                        graph[name].add(value.name)
                    elif value.name not in TA_ARITY and value.name not in MATH_ARITY and value.name not in ("na", "nz"):
                        raise self.error(value.token, "UNSUPPORTED_CALL", "v1 暂不支持调用 " + value.name)
                    pending.extend(value.args)
                    pending.extend(item for _, item in value.keywords)
                elif isinstance(value, ast.Binary):
                    pending.extend((value.left, value.right))
                elif isinstance(value, ast.Unary):
                    pending.append(value.value)
                elif isinstance(value, ast.History):
                    pending.extend((value.value, value.offset))
                elif isinstance(value, ast.Conditional):
                    pending.extend((value.test, value.yes, value.no))
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(name: str) -> None:
            if name in visiting:
                raise self.error(self.functions[name].token, "RECURSION", "不支持递归函数")
            if name in visited:
                return
            if len(visiting) >= 50:
                raise self.error(self.functions[name].token, "NESTING_LIMIT", "函数调用不能超过 50 层")
            visiting.add(name)
            for called in graph[name]:
                visit(called)
            visiting.remove(name)
            visited.add(name)
        for name in graph:
            visit(name)


def compile_source(source: str, *, identity: str = "custom.preview", revision: int = 1,
                   language: SourceKind = "pine") -> Program:
    try:
        return Compiler(source, identity, revision, language).compile()
    except (RecursionError, OverflowError):
        raise ScriptError(1, 1, "RESOURCE_LIMIT", "源码复杂度超过 v1 限制") from None


def diagnostics(source: str) -> tuple[Diagnostic, ...]:
    try:
        compile_source(source)
        return ()
    except ScriptError as exc:
        return (exc.diagnostic,)
    except (RecursionError, OverflowError):
        return (Diagnostic("error", 1, 1, "RESOURCE_LIMIT", "源码复杂度超过 v1 限制"),)
