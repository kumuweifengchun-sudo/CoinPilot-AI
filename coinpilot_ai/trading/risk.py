"""按线性 USDT 合约规格计算计划仓位，不产生订单。"""
from decimal import Decimal, ROUND_FLOOR

from .models import number


def position_plan(*, equity, risk_percent, entry, stop, target=None, direction="long", spec):
    if direction not in ("long", "short"):
        raise ValueError("请选择做多或做空")
    equity = number(equity, "账户资产", positive=True)
    risk_percent = number(risk_percent, "风险比例", positive=True)
    entry = number(entry, "入场价", positive=True)
    stop = number(stop, "止损价", positive=True)
    if risk_percent > 100:
        raise ValueError("风险比例不能超过 100%")
    if (direction == "long" and stop >= entry) or (direction == "short" and stop <= entry):
        raise ValueError("止损价与交易方向不匹配")
    target_value = number(target, "止盈价", positive=True) if target not in (None, "") else None
    if target_value is not None and ((direction == "long" and target_value <= entry) or
                                     (direction == "short" and target_value >= entry)):
        raise ValueError("止盈价与交易方向不匹配")
    if spec.get("ctType") != "linear" or spec.get("settleCcy") != "USDT":
        raise ValueError("仅支持 USDT 本位线性永续")
    unit = number(spec["ctVal"], "每张面值", positive=True) * number(spec.get("ctMult") or "1", "合约乘数", positive=True)
    if spec.get("ctValCcy") not in (None, "", spec.get("instId", "").split("-")[0]):
        raise ValueError("合约面值单位不是基础币种")
    lot = number(spec["lotSz"], "数量步长", positive=True)
    minimum = number(spec["minSz"], "最小张数", positive=True)
    budget = equity * risk_percent / Decimal(100)
    distance = abs(entry - stop)
    contracts = (budget / (distance * unit) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    if contracts < minimum:
        contracts = Decimal(0)
    loss = contracts * unit * distance
    reward = contracts * unit * abs(target_value-entry) if target_value is not None else None
    return {"direction": direction, "risk_budget": budget, "stop_distance": distance,
            "stop_percent": distance / entry * 100, "contracts": contracts,
            "base_quantity": contracts * unit, "notional": contracts * unit * entry,
            "planned_loss": loss, "risk_reward": (reward / loss if loss and reward is not None else None),
            "planned_reward": reward, "below_minimum": contracts == 0}
