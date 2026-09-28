"""图表多空测算；金额为入场时的仓位名义价值，不关联订单。"""
from decimal import Decimal, InvalidOperation


POSITION_TOOLS = {"long_position": "开多", "short_position": "开空"}


def position_metrics(obj):
    if obj.get("tool") not in POSITION_TOOLS or len(obj.get("anchors", [])) != 3:
        raise ValueError("多空图形缺少价格")
    try:
        entry, target, stop = (Decimal(str(anchor[1])) for anchor in obj["anchors"])
        notional = Decimal(str(obj.get("notional_usdt", "")))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("请输入有效的入场价、止盈价、止损价和仓位金额") from None
    if not all(value.is_finite() and value > 0 for value in (entry, target, stop, notional)):
        raise ValueError("价格和仓位金额必须为正数")
    if obj["tool"] == "long_position":
        valid = target > entry > stop
    else:
        valid = target < entry < stop
    if not valid:
        raise ValueError("止盈、止损价与开仓方向不匹配")
    profit_percent = abs(target-entry) / entry * 100
    loss_percent = abs(stop-entry) / entry * 100
    return {"entry": entry, "target": target, "stop": stop, "notional": notional,
            "profit_percent": profit_percent, "loss_percent": loss_percent,
            "profit_usdt": notional * profit_percent / 100,
            "loss_usdt": notional * loss_percent / 100}


def position_summary(obj):
    result = position_metrics(obj)
    return (f"{POSITION_TOOLS[obj['tool']]} · 仓位 {result['notional']:,.2f} USDT · "
            f"预计盈利 +{result['profit_usdt']:,.2f} USDT ({result['profit_percent']:.2f}%) · "
            f"预计亏损 -{result['loss_usdt']:,.2f} USDT ({result['loss_percent']:.2f}%)")
