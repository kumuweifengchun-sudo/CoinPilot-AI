"""JSON 边界、定义输入校验和 v1 实例迁移。"""
import math
from collections.abc import Mapping
from typing import TypedDict

from coinpilot_ai.market.intervals import BARS
from .catalog import BUILTIN_CATALOG, IndicatorCatalog
from .models import IndicatorDefinition, IndicatorInstance, InputDefinition, InputValue, PlotStyle


class LegacySpec(TypedDict):
    kind: str
    params: dict[str, int | float]


def input_value(definition: InputDefinition, value: object) -> InputValue:
    if definition.type == "bool":
        if not isinstance(value, bool):
            raise ValueError(definition.title + " 须为布尔值")
        return value
    if definition.type == "source":
        if value not in ("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4"):
            raise ValueError(definition.title + " 价格来源无效")
        if not isinstance(value, str):
            raise ValueError("价格来源须为字符串")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(definition.title + " 须为有限数值")
    if definition.type == "int" and int(value) != value:
        raise ValueError(definition.title + " 须为整数")
    if ((definition.minimum is not None and value < definition.minimum)
            or (definition.maximum is not None and value > definition.maximum)):
        raise ValueError(definition.title + " 超出指标输入范围")
    return int(value) if definition.type == "int" else float(value)


def validate_inputs(definition: IndicatorDefinition, supplied: Mapping[str, object]) -> dict[str, InputValue]:
    known = {item.id for item in definition.inputs}
    if set(supplied) - known:
        raise ValueError("指标包含未知输入")
    values = {item.id: input_value(item, supplied.get(item.id, item.default)) for item in definition.inputs}
    for first, second in definition.ordered_inputs:
        a, b = values[first], values[second]
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or a >= b:
            raise ValueError(first + " 须小于 " + second)
    return values


def legacy_spec(value: object, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> LegacySpec:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise ValueError("不支持的指标")
    definition = catalog.legacy(value["kind"])
    params = value.get("params", {})
    if not isinstance(params, dict) or any(not isinstance(key, str) for key in params):
        raise ValueError("指标参数须为对象")
    inputs = validate_inputs(definition, {key: item for key, item in params.items()})
    numbers: dict[str, int | float] = {}
    for key, item in inputs.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("旧版内置指标仅支持数值输入")
        numbers[key] = item
    return {"kind": definition.name, "params": numbers}


def _style(value: object, fallback: PlotStyle) -> PlotStyle:
    if not isinstance(value, dict):
        raise ValueError("绘图样式须为对象")
    color = value.get("color", fallback.color)
    width = value.get("width", fallback.width)
    visible = value.get("visible", fallback.visible)
    if (not isinstance(color, str) or len(color) not in (7, 9) or not color.startswith("#")
            or any(char not in "0123456789abcdefABCDEF" for char in color[1:])):
        raise ValueError("指标颜色无效")
    if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or not .5 <= width <= 6:
        raise ValueError("线宽须为 0.5—6")
    if not isinstance(visible, bool):
        raise ValueError("指标显示状态须为布尔值")
    return PlotStyle(visible, color, float(width))


def instance_from_record(value: object, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> IndicatorInstance:
    if not isinstance(value, dict):
        raise ValueError("指标实例须为对象")
    identity = value.get("id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("指标实例 ID 无效")
    legacy = "definition" not in value
    if legacy:
        spec = legacy_spec(value, catalog)
        definition = catalog.legacy(spec["kind"])
        inputs: Mapping[str, object] = spec["params"]
    else:
        definition_id = value.get("definition")
        revision = value.get("definition_revision", value.get("revision"))
        if not isinstance(definition_id, str) or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("指标定义引用无效")
        definition = catalog.resolve(definition_id, revision)
        raw = value.get("inputs", {})
        if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
            raise ValueError("指标输入须为对象")
        inputs = {key: item for key, item in raw.items()}
    timeframe = value.get("timeframe", value.get("bar", "chart"))
    if not isinstance(timeframe, str) or (timeframe != "chart" and timeframe not in BARS):
        raise ValueError("无效的指标周期")
    if definition.source != "builtin" and timeframe != "chart":
        raise ValueError("脚本 v1 仅支持图窗当前周期")
    common = _style(value, PlotStyle())
    raw_plots = value.get("plots", {})
    if not isinstance(raw_plots, dict) or set(raw_plots) - {plot.id for plot in definition.plots}:
        raise ValueError("指标绘图引用无效")
    styles = {plot.id: _style(raw_plots.get(plot.id, {}), common if legacy else PlotStyle(color=plot.color, width=plot.width))
              for plot in definition.plots}
    visible = value.get("visible", True)
    if not isinstance(visible, bool):
        raise ValueError("指标显示状态须为布尔值")
    return IndicatorInstance(identity, definition.id, definition.revision, validate_inputs(definition, inputs),
                             timeframe, styles, visible)


def instance_record(instance: IndicatorInstance) -> dict[str, object]:
    return {"id": instance.id, "definition": instance.definition, "definition_revision": instance.definition_revision,
            "inputs": dict(instance.inputs), "timeframe": instance.timeframe, "visible": instance.visible,
            "plots": {key: {"visible": style.visible, "color": style.color, "width": style.width}
                      for key, style in instance.plots.items()}}


def runtime_instance(value: object, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> IndicatorInstance:
    if isinstance(value, IndicatorInstance):
        definition = catalog.resolve(value.definition, value.definition_revision)
        validate_inputs(definition, value.inputs)
        return value
    if not isinstance(value, dict):
        raise ValueError("指标配置须为对象")
    return instance_from_record(dict(value, id=value.get("id", "runtime")), catalog)


def migrate_instances(value: object, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> dict[str, object]:
    if isinstance(value, dict):
        if value.get("version") not in (1, 2):
            raise ValueError("未知的指标实例记录版本")
        value = value.get("items")
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("指标实例列表无效或过多")
    items = [instance_from_record(item, catalog) for item in value]
    if len({item.id for item in items}) != len(items):
        raise ValueError("指标实例 ID 重复")
    return {"version": 2, "items": [instance_record(item) for item in items]}
