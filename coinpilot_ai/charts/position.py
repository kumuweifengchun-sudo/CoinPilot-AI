"""图表多空测算；金额为入场时的仓位名义价值，不关联订单。"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


POSITION_TOOLS = {"long_position": "开多", "short_position": "开空"}


class PositionInputError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def position_value(value, field):
    text = str(value).strip()
    if not text:
        raise PositionInputError(field, f"请填写{field}")
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise PositionInputError(field, f"{field}必须是有效数字") from None
    if not result.is_finite() or result <= 0:
        raise PositionInputError(field, f"{field}必须是大于 0 的有限数字")
    return result


def position_price(value, tick_size=None):
    """按合约报价步长四舍五入；规格未就绪时不猜测价格精度。"""
    price = Decimal(str(value))
    try:
        tick = Decimal(str(tick_size))
    except InvalidOperation:
        tick = None
    if tick is not None and tick.is_finite() and tick > 0 and price.is_finite():
        price = (price/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick
    return format(price, 'f')


def position_metrics(obj):
    if obj.get("tool") not in POSITION_TOOLS or len(obj.get("anchors", [])) != 3:
        raise ValueError("多空图形缺少价格")
    entry, target, stop = (position_value(anchor[1], field) for anchor, field in
                          zip(obj["anchors"], ("入场价", "止盈价", "止损价")))
    notional = position_value(obj.get("notional_usdt", ""), "仓位金额")
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
