"""训练回放与规则回测共用的 OHLC 保护单判断。"""
from decimal import Decimal


def execution_price(raw_price, side, kind, slippage_bps):
    """市价单按买卖方向应用滑点；限价单保持给定撮合价。"""
    if kind != "market":
        return raw_price
    sign = 1 if side == "buy" else -1
    return raw_price*(1+Decimal(sign)*slippage_bps/Decimal(10000))


def execution_fee(price, quantity, contract_unit, fee_rate):
    return price*quantity*contract_unit*fee_rate


def protective_exit(opened, high, low, stop, target, *, long):
    """同根止盈止损均触及时按先止损；止损跳空按更不利开盘价。"""
    hit_stop = stop is not None and (low <= stop if long else high >= stop)
    hit_target = target is not None and (high >= target if long else low <= target)
    if not (hit_stop or hit_target):
        return None
    if hit_stop:
        price = min(opened, stop) if long else max(opened, stop)
        return price, "stop", bool(hit_target)
    return target, "target", False
