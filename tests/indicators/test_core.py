"""平台定义、旧实例迁移与真实增量路径的数值回归。"""
from dataclasses import replace
import math

import pytest

from coinpilot_ai.indicators.builtin.engine import _reference
from coinpilot_ai.indicators.catalog import BUILTIN_CATALOG, IndicatorCatalog
from coinpilot_ai.indicators.models import IndicatorCacheKey, IndicatorInstance
from coinpilot_ai.indicators.runtime.engine import IndicatorRuntime, evaluate
from coinpilot_ai.indicators.validation import (instance_from_record, instance_record, legacy_spec,
                                               migrate_instances, validate_inputs)
from coinpilot_ai.market.buffer import CandleBuffer


def rows(count: int) -> CandleBuffer:
    return CandleBuffer((300000 + index * 60000, 100. + math.sin(index / 7), 104., 96.,
                         100. + math.sin(index / 7), 10. + index % 5, 0., 0., 1) for index in range(count))


def test_catalog_versions_are_immutable_and_inputs_describe_all_builtins() -> None:
    assert len(BUILTIN_CATALOG.list()) == 12
    catalog = IndicatorCatalog()
    definition = catalog.get("builtin.ema")
    assert definition.inputs[0].default == 20
    assert definition.plots[0].target == "overlay"
    assert catalog.get("builtin.rsi").pane.scale.maximum == 100
    assert catalog.get("builtin.macd").plots[-1].type == "histogram"
    catalog.register(replace(definition, revision=2, title="EMA v2"))
    assert catalog.resolve(definition.id).revision == 2
    assert catalog.resolve(definition.id, 1) == definition
    with pytest.raises(ValueError, match="不可覆盖"):
        catalog.register(replace(definition, title="悄悄覆盖"))
    with pytest.raises(ValueError):
        validate_inputs(catalog.get("builtin.macd"), {"fast": 30, "slow": 20})


@pytest.mark.parametrize("params", [{"period": True}, {"period": 1.5}, {"period": 0},
                                    {"period": float("nan")}, {"unused": 3}])
def test_numeric_input_boundary_rejects_unsafe_or_unknown_values(params: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        legacy_spec({"kind": "EMA", "params": params})


def test_v1_migration_roundtrip_preserves_instance_and_plot_style() -> None:
    old = {"id": "existing", "kind": "MACD", "params": {"fast": 9, "slow": 21, "signal": 5},
           "bar": "1H", "visible": True, "color": "#112233", "width": 2.5}
    migrated = migrate_instances([old])
    assert migrated["version"] == 2
    assert migrated == migrate_instances(migrated)
    values = migrated["items"]
    assert isinstance(values, list)
    instance = instance_from_record(values[0])
    assert instance.definition == "builtin.macd" and instance.id == "existing"
    assert instance.timeframe == "1H" and instance.inputs["fast"] == 9
    assert instance.plots["histogram"].color == "#112233"
    assert instance_from_record(instance_record(instance)) == instance
    assert "definition" not in old
    with pytest.raises(TypeError):
        instance.inputs["fast"] = 99  # pyright: ignore[reportIndexIssue] -- 验证只读 Mapping 的运行时行为。


def test_cache_key_contains_revision_inputs_and_timeframe_but_not_display_style() -> None:
    instance = instance_from_record({"id": "chart-a", "kind": "EMA", "params": {"period": 9}})
    key = IndicatorCacheKey.of(instance, "1m")
    assert key == IndicatorCacheKey.of(replace(instance, id="chart-b", plots={}), "1m")
    assert key != IndicatorCacheKey.of(replace(instance, definition_revision=2), "1m")
    assert key != IndicatorCacheKey.of(replace(instance, inputs={"period": 10}), "1m")
    assert key != IndicatorCacheKey.of(instance, "5m")


@pytest.mark.parametrize("definition", BUILTIN_CATALOG.list(), ids=lambda value: value.name)
def test_runtime_matches_independent_full_reference_and_only_revises_tail(definition) -> None:
    data = rows(350)
    instance = IndicatorInstance("runtime", definition.id, definition.revision)
    runtime = IndicatorRuntime(instance, bar="1m")
    runtime.update(data[:349], 0)
    old_buffers = runtime.times, runtime.confirmed, dict(runtime.result.outputs)
    before = runtime.processed_rows
    result = runtime.update(data, 349)
    assert runtime.processed_rows == before + 1
    assert runtime.times is old_buffers[0] and runtime.confirmed is old_buffers[1]
    assert all(runtime.result.outputs[key] is value for key, value in old_buffers[2].items())
    assert result.definition_id == definition.id and result.revision == definition.revision
    assert result.metadata.timeframe == "1m"
    expected = _reference(data, {"kind": definition.name}, bar="1m")
    for name, values in expected.outputs.items():
        assert list(result.outputs[name]) == pytest.approx(values, abs=2e-9)
    changed = list(data[-1])
    changed[4] = 102.
    changed[8] = 0
    data[-1] = changed
    before = runtime.processed_rows
    result = runtime.update(data, 349)
    assert runtime.processed_rows == before + 1
    expected = _reference(data, {"kind": definition.name}, bar="1m")
    for name, values in expected.outputs.items():
        assert list(result.outputs[name]) == pytest.approx(values, abs=2e-9)
    assert not result.confirmed[-1]
    # 缺口前的递归状态不得影响缺口之后的预热。
    del data[120]
    rebuilt = runtime.update(data, 0)
    expected = _reference(data, {"kind": definition.name}, bar="1m")
    for name, values in expected.outputs.items():
        assert list(rebuilt.outputs[name]) == pytest.approx(values, abs=2e-9)


def test_multiple_runtimes_borrow_one_buffer_without_copying_candles() -> None:
    data = rows(1000)
    before = data.revision, data.nbytes
    for definition in BUILTIN_CATALOG.list():
        result = evaluate(IndicatorInstance(definition.id, definition.id, 1), data, bar="1m")
        assert len(result.times) == 1000
    assert (data.revision, data.nbytes) == before
    assert len(data) == 1000
