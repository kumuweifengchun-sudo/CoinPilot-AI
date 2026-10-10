"""提醒、回测与扫描共享指标条件契约；未知值不会满足条件。"""
from collections.abc import Mapping
import math

from .catalog import BUILTIN_CATALOG, IndicatorCatalog
from .models import IndicatorInstance
from .validation import runtime_instance


def spec_record(instance: IndicatorInstance) -> dict[str, object]:
    return {"definition": instance.definition, "definition_revision": instance.definition_revision,
            "inputs": dict(instance.inputs)}


def spec_title(value: object, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> str:
    try:
        instance = runtime_instance(value, catalog)
        return catalog.resolve(instance.definition, instance.definition_revision).name
    except ValueError:
        return "不可用指标"


def validate_condition(condition: Mapping[str, object], catalog: IndicatorCatalog = BUILTIN_CATALOG) -> None:
    instance = runtime_instance(condition.get("indicator"), catalog)
    definition = catalog.resolve(instance.definition, instance.definition_revision)
    line = condition.get("line")
    line = line or (definition.outputs[0].id if definition.outputs else "")
    output = next((item for item in definition.outputs if item.id == line), None)
    if output is None:
        raise ValueError("指标输出不存在")
    op = condition.get("op")
    if output.type == "bool":
        if op not in ("is_true", "is_false") or "compare" in condition:
            raise ValueError("布尔输出使用 为真／为假 条件")
        return
    if op not in ("above", "below", "cross_above", "cross_below"):
        raise ValueError("指标比较方式无效")
    other = condition.get("compare")
    if other is not None:
        if not isinstance(other, dict):
            raise ValueError("比较指标无效")
        rhs = runtime_instance(other.get("indicator"), catalog)
        right_definition = catalog.resolve(rhs.definition, rhs.definition_revision)
        right_line = other.get("line") or right_definition.outputs[0].id
        right = next((item for item in right_definition.outputs if item.id == right_line), None)
        if right is None or right.type == "bool":
            raise ValueError("数值条件需要数值比较输出")
    else:
        from coinpilot_ai.trading.models import number
        number(condition.get("threshold"))


def compare_values(op: str, previous: float | bool | None, current: float | bool | None,
                   rhs_previous: float | bool | None = None, rhs_current: float | bool | None = None) -> bool | None:
    if current is None:
        return None
    if op in ("is_true", "is_false"):
        return current is (op == "is_true") if isinstance(current, bool) else None
    if rhs_current is None or isinstance(current, bool) or isinstance(rhs_current, bool):
        return None
    if not math.isfinite(current) or not math.isfinite(rhs_current):
        return None
    if op == "above":
        return current >= rhs_current
    if op == "below":
        return current <= rhs_current
    if previous is None or rhs_previous is None or isinstance(previous, bool) or isinstance(rhs_previous, bool):
        return None
    if op == "cross_above":
        return previous <= rhs_previous and current > rhs_current
    if op == "cross_below":
        return previous >= rhs_previous and current < rhs_current
    raise ValueError("未知的指标比较方式")
