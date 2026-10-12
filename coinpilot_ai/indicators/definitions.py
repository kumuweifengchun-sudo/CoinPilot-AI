"""内置指标的输入、输出、绘图与轴元数据；不依赖 Qt。"""
from .models import (IndicatorDefinition, InputDefinition, OutputDefinition,
                     PaneDefinition, PlotDefinition, PlotTarget, ScaleDefinition)


def _integer(key: str, default: int) -> InputDefinition:
    return InputDefinition(key, key, "int", default, 1, 1000)


def _multiplier(key: str, default: float) -> InputDefinition:
    return InputDefinition(key, key, "float", default, .01, 100)


def _definition(name: str, inputs: tuple[InputDefinition, ...], outputs: tuple[str, ...] = ("value",), *,
                target: PlotTarget = "overlay", scale: ScaleDefinition = ScaleDefinition(),
                histogram: str | None = None, ordered: tuple[tuple[str, str], ...] = ()) -> IndicatorDefinition:
    return IndicatorDefinition("builtin." + name.lower(), name, name, "builtin", 1, inputs,
        tuple(OutputDefinition(key, key) for key in outputs),
        tuple(PlotDefinition(key, key, key, "histogram" if key == histogram else "line", target)
              for key in outputs), PaneDefinition(target, scale), ordered)


BUILTIN_DEFINITIONS: tuple[IndicatorDefinition, ...] = (
    _definition("MA", (_integer("period", 20),)),
    _definition("EMA", (_integer("period", 20),)),
    _definition("RSI", (_integer("period", 14),), target="pane",
                scale=ScaleDefinition("fixed", 0, 100, (30, 70))),
    _definition("MACD", (_integer("fast", 12), _integer("slow", 26), _integer("signal", 9)),
                ("macd", "signal", "histogram"), target="pane", histogram="histogram",
                scale=ScaleDefinition(levels=(0,)), ordered=(("fast", "slow"),)),
    _definition("BOLL", (_integer("period", 20), _multiplier("deviations", 2)), ("middle", "upper", "lower")),
    IndicatorDefinition("builtin.donchian", "DONCHIAN", "DONCHIAN · 唐奇安通道", "builtin", 1,
        (InputDefinition("period", "周期", "int", 20, 1, 1000),),
        (OutputDefinition("upper", "上轨"), OutputDefinition("middle", "中轨"), OutputDefinition("lower", "下轨")),
        (PlotDefinition("upper", "上轨", "upper", color="#35a4ff"),
         PlotDefinition("middle", "中轨", "middle", color="#f5a623"),
         PlotDefinition("lower", "下轨", "lower", color="#35a4ff"))),
    _definition("ATR", (_integer("period", 14),), target="pane"),
    IndicatorDefinition("builtin.dmi", "DMI", "DMI · 动向指标", "builtin", 1,
        (InputDefinition("period", "DI 周期", "int", 14, 1, 1000),
         InputDefinition("adx_period", "ADX 平滑周期", "int", 14, 1, 1000)),
        (OutputDefinition("plus_di", "+DI"), OutputDefinition("minus_di", "-DI"), OutputDefinition("adx", "ADX")),
        (PlotDefinition("plus_di", "+DI", "plus_di", target="pane", color="#20c997"),
         PlotDefinition("minus_di", "-DI", "minus_di", target="pane", color="#f06472"),
         PlotDefinition("adx", "ADX", "adx", target="pane", color="#f5a623", width=2.)),
        PaneDefinition("pane", ScaleDefinition("fixed", 0, 100, (25,)))),
    _definition("VWAP", ()),
    _definition("VOLUME_MA", (_integer("period", 20),), target="pane"),
    _definition("STOCHASTIC", (_integer("period", 14), _integer("smooth", 3)), ("k", "d"),
                target="pane", scale=ScaleDefinition("fixed", 0, 100, (20, 80))),
    _definition("SUPERTREND", (_integer("period", 10), _multiplier("multiplier", 3))),
)
