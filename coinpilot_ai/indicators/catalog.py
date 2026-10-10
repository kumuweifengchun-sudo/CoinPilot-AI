"""按不可变版本解析指标。自定义目录由业务服务持有，不跨用户配置共享。"""
from .definitions import BUILTIN_DEFINITIONS
from .models import IndicatorDefinition
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .script.ir import Program


class IndicatorCatalog:
    def __init__(self, *, builtins: bool = True) -> None:
        self._definitions: dict[tuple[str, int], IndicatorDefinition] = {}
        self._hidden: set[str] = set()
        self._programs: dict[tuple[str, int], "Program"] = {}
        if builtins:
            for definition in BUILTIN_DEFINITIONS:
                self.register(definition)

    def register(self, definition: IndicatorDefinition) -> None:
        if not definition.id or not definition.name or definition.revision < 1:
            raise ValueError("指标定义标识、名称或版本无效")
        inputs = [item.id for item in definition.inputs]
        outputs = [item.id for item in definition.outputs]
        plots = [item.id for item in definition.plots]
        if (len(inputs) > 64 or len(plots) > 32 or len(inputs) != len(set(inputs))
                or len(outputs) != len(set(outputs)) or len(plots) != len(set(plots))
                or any(not key for key in (*inputs, *outputs, *plots))):
            raise ValueError("指标字段重复或超过资源限制")
        if any(plot.output not in outputs for plot in definition.plots):
            raise ValueError("绘图引用的输出不存在")
        if any(a not in inputs or b not in inputs for a, b in definition.ordered_inputs):
            raise ValueError("指标输入约束引用不存在")
        key = definition.id, definition.revision
        old = self._definitions.get(key)
        if old is not None and old != definition:
            raise ValueError("已有指标版本不可覆盖，请增加 revision")
        self._definitions[key] = definition

    def resolve(self, identity: str, revision: int | None = None) -> IndicatorDefinition:
        if revision is None:
            revisions = [number for key, number in self._definitions if key == identity]
            if not revisions:
                raise ValueError("指标定义不存在：" + identity)
            revision = max(revisions)
        definition = self._definitions.get((identity, revision))
        if definition is None:
            raise ValueError("指标定义版本不存在：" + identity)
        return definition

    def get(self, identity: str) -> IndicatorDefinition:
        return self.resolve(identity)

    def register_program(self, program: "Program") -> None:
        if program.definition.source == "builtin":
            raise ValueError("脚本不能覆盖内置执行器")
        self.register(program.definition)
        key = program.definition.id, program.definition.revision
        old = self._programs.get(key)
        if old is not None and old != program:
            raise ValueError("已有脚本程序版本不可覆盖")
        self._programs[key] = program

    def program(self, identity: str, revision: int) -> "Program":
        program = self._programs.get((identity, revision))
        if program is None:
            raise ValueError("指标定义没有可用执行程序")
        return program

    def legacy(self, kind: str) -> IndicatorDefinition:
        definition = self.resolve("builtin." + kind.lower())
        if definition.name != kind:
            raise ValueError("不支持的指标")
        return definition

    def list(self, *, source: str | None = None, include_hidden: bool = False) -> tuple[IndicatorDefinition, ...]:
        identities = sorted({key for key, _ in self._definitions})
        return tuple(definition for key in identities if (include_hidden or key not in self._hidden)
                     for definition in (self.resolve(key),) if source is None or definition.source == source)

    def hide(self, identity: str) -> None:
        self.resolve(identity)
        if identity.startswith("builtin."):
            raise ValueError("不能隐藏内置指标定义")
        self._hidden.add(identity)

    def remove(self, identity: str) -> None:
        if identity.startswith("builtin."):
            raise ValueError("不能删除内置指标")
        for key in tuple(self._definitions):
            if key[0] == identity:
                self._definitions.pop(key)
                self._programs.pop(key, None)
        self._hidden.discard(identity)


BUILTIN_CATALOG = IndicatorCatalog()
