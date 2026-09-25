"""图表的持久化状态。锚点保存交易所毫秒时间与价格，不保存屏幕像素。"""
from copy import deepcopy
from coinpilot_ai.ui.theme import color as theme_color
import math
import uuid

from PyQt6.QtCore import QObject, pyqtSignal

from coinpilot_ai.market.intervals import BARS
TOOLS = {"cursor": "光标", "trend": "趋势线", "horizontal": "水平线", "ray": "射线",
         "vertical": "竖线", "rectangle": "矩形", "text": "文字", "fib": "斐波拉契", "measure": "测距"}
OBJECT_NAMES = dict(TOOLS, region="填色区域")
def default_emas():
    return [{"period": 20, "color": theme_color("ema_fast"), "width": 1.5, "visible": True},
            {"period": 60, "color": theme_color("ema_slow"), "width": 1.5, "visible": True}]


DEFAULT_EMAS = default_emas()
FIB_LEVELS = [0, .236, .382, .5, .618, .786, 1]


def drawing(tool, anchors, text=""):
    return {"id": uuid.uuid4().hex, "tool": tool, "anchors": deepcopy(anchors), "text": text,
            "color": theme_color("focus"), "width": 1.5, "style": "solid", "locked": False, "hidden": False,
            "bars": list(BARS), "levels": [{"value": n, "label": f"{n:g}", "color": theme_color("focus")} for n in FIB_LEVELS]}


def name_drawings(objects):
    """用当前最小可用编号给未命名对象命名，保留已有名称。"""
    used = {o.get('name') for o in objects if o.get('name')}
    next_indices = {}
    for obj in objects:
        if str(obj.get('name') or '').strip():
            continue
        tool, prefix = obj['tool'], OBJECT_NAMES[obj['tool']]
        index = next_indices.get(tool, 1)
        while prefix+str(index) in used:
            index += 1
        obj['name'] = prefix+str(index)
        next_indices[tool] = index+1
        used.add(obj['name'])


def validate_emas(items):
    result = []
    for item in items:
        period = int(item["period"])
        if not 1 <= period <= 1000:
            raise ValueError("EMA 周期须为 1—1000 的整数")
        width = float(item.get("width", 1.5))
        if not math.isfinite(width) or not .5 <= width <= 6:
            raise ValueError("线宽须为 0.5—6")
        result.append(dict(item, period=period, width=width))
    return result


def normalize_indicators(items):
    from coinpilot_ai.market.indicators import validate_spec
    cleaned = []
    for item in items:
        spec = validate_spec(item)
        bar = item.get("bar", "chart")
        if bar != "chart" and bar not in BARS:
            raise ValueError("无效的指标周期")
        width = float(item.get("width", 1.5))
        if not math.isfinite(width) or not .5 <= width <= 6:
            raise ValueError("线宽须为 0.5—6")
        cleaned.append({"id": str(item["id"]), **spec, "bar": bar,
                        "color": str(item.get("color", "#35a4ff")),
                        "width": width, "visible": bool(item.get("visible", True))})
    return cleaned


def ema_values(closes, period):
    if not closes:
        return []
    alpha, value = 2 / (period + 1), closes[0]
    result = []
    for close in closes:
        value += alpha * (close - value)
        result.append(value)
    return result


class ChartBook(QObject):
    changed = pyqtSignal(str, object)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.undo_stacks, self.redo_stacks = {}, {}
        self.cache = {}
        self.emas = store.get("chart_settings", "ema", default_emas())
        indicators = store.get("chart_settings", "indicators_v1")
        if indicators is None:
            indicators = [{"id": uuid.uuid4().hex, "kind": "EMA", "params": {"period": row["period"]}, "bar": "chart",
                           "color": row["color"], "width": row["width"], "visible": row["visible"]}
                          for row in self.emas]
            store.put("chart_settings", "indicators_v1", indicators)
        self.indicators = indicators
        self.magnet_enabled = store.get("chart_settings", "magnet", True) is not False

    def save_magnet(self, enabled):
        enabled = bool(enabled)
        if enabled != self.magnet_enabled:
            self.magnet_enabled = enabled
            self.store.put("chart_settings", "magnet", enabled)
            self.changed.emit("magnet", None)

    def objects(self, environment, instrument):
        key = (environment, instrument)
        if key not in self.cache:
            objects = self.store.get("chart_drawings", instrument, [], environment)
            original = deepcopy(objects)
            name_drawings(objects)
            if original != objects:
                self.store.put('chart_drawings', instrument, objects, environment)
            self.cache[key] = objects
        return deepcopy(self.cache[key])

    def save_objects(self, environment, instrument, objects, *, history=True):
        key = (environment, instrument)
        before = self.objects(*key)
        name_drawings(objects)
        if before == objects:
            return
        self.store.put('chart_drawings', instrument, objects, environment)
        if history:
            self.undo_stacks.setdefault(key, []).append(before)
            self.undo_stacks[key] = self.undo_stacks[key][-100:]
            self.redo_stacks[key] = []
        self.cache[key] = deepcopy(objects)
        self.changed.emit("drawings", key)

    def undo(self, environment, instrument, redo=False):
        key = (environment, instrument)
        source = self.redo_stacks if redo else self.undo_stacks
        target = self.undo_stacks if redo else self.redo_stacks
        if source.get(key):
            target.setdefault(key, []).append(self.objects(*key))
            self.save_objects(*key, source[key].pop(), history=False)

    def view(self, environment, instrument, bar):
        return self.store.get("chart_view", instrument + "/" + bar, {}, environment)

    def save_view(self, environment, instrument, bar, view):
        self.store.put("chart_view", instrument + "/" + bar, view, environment)
        self.changed.emit("view", (environment, instrument, bar))

    def save_emas(self, items):
        self.emas = validate_emas(items)
        self.store.put("chart_settings", "ema", self.emas)
        self.changed.emit("ema", None)

    def save_indicators(self, items):
        cleaned = normalize_indicators(items)
        self.store.put("chart_settings", "indicators_v1", cleaned)
        self.indicators = cleaned
        self.changed.emit("indicators", None)


def trade_lines(service):
    """只读快照；不存在通往下单、撤单或改单的回调。"""
    result = []
    def add(raw, label, color):
        try:
            value = float(raw)
            if math.isfinite(value) and value > 0:
                result.append({"price": value, "label": label, "color": color})
        except (ValueError, TypeError):
            pass
    for p in service.positions:
        if p.get("instId") != service.selected or not float(p.get("pos") or 0):
            continue
        side = p.get("posSide", "net")
        side = ("long" if float(p["pos"]) > 0 else "short") if side == "net" else side
        add(p.get("avgPx"), f"{'多' if side == 'long' else '空'}仓均价 · {abs(float(p['pos'])):g} 张", theme_color("warning"))
    for o in service.pending_orders:
        if o.get("instId") == service.selected:
            add(o.get("px"), f"{'买' if o.get('side') == 'buy' else '卖'}挂单 · {o.get('sz', '')} 张", theme_color("focus"))
    for o in service.algos:
        if o.get("instId") == service.selected:
            for field, name, line_color in (("slTriggerPx", "止损", theme_color("negative")), ("tpTriggerPx", "止盈", theme_color("positive"))):
                kind = o.get(field.replace("Px", "PxType"), "last")
                kind = {"last": "最新价", "mark": "标记价", "index": "指数价"}.get(kind, kind)
                add(o.get(field), f"{'买' if o.get('side') == 'buy' else '卖'}{name}触发 · {o.get('sz', '')} 张 · {kind}", line_color)
    return result
