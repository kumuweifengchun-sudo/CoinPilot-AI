"""交易和行情的纯数据规则，所有金额与合约数量均使用 Decimal。"""
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation


def number(value, name="数值", *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name}必须是有效数字") from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{name}必须是{'正' if positive else '有限'}数")
    return result


def instrument_id(value):
    value = value.strip().upper()
    if re.fullmatch(r"[A-Z0-9]+USDT", value):
        value = value[:-4] + "-USDT-SWAP"
    if not re.fullmatch(r"[A-Z0-9]+-USDT-SWAP", value):
        raise ValueError("仅支持 OKX USDT 永续，例如 BTC-USDT-SWAP")
    return value


def aligned(value, step, name):
    value, step = number(value, name, positive=True), number(step, "交易精度", positive=True)
    if value % step:
        raise ValueError(f"{name}必须是 {step} 的整数倍")
    return value


@dataclass
class OrderDraft:
    instrument: str
    action: str = "open"
    direction: str = "long"
    order_type: str = "market"
    size: str = ""
    price: str = ""
    margin: str = "cross"
    stop_loss: str = ""
    take_profit: str = ""
    reason: str = ""
    tags: str = ""
    source: str = "manual"

    @classmethod
    def from_ai(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("AI 订单草稿必须是 JSON 对象")
        allowed = {key for key in cls.__dataclass_fields__} - {"source"}
        values = {key: str(value) for key, value in raw.items() if key in allowed and value is not None}
        if "instrument" not in values:
            raise ValueError("AI 草稿缺少合约")
        values["instrument"] = instrument_id(values["instrument"])
        return cls(**values, source="ai")

    def payload(self, spec, account, positions, quote, now, client_id):
        self.instrument = instrument_id(self.instrument)
        if self.action not in ("open", "close") or self.direction not in ("long", "short"):
            raise ValueError("开平仓或方向无效")
        if self.order_type not in ("market", "limit") or self.margin not in ("cross", "isolated"):
            raise ValueError("订单类型或保证金模式无效")
        if spec.get("instId") != self.instrument or spec.get("state") != "live":
            raise ValueError("合约信息不可用或交易已暂停")
        if spec.get("ctType") != "linear" or spec.get("settleCcy") != "USDT":
            raise ValueError("仅支持 USDT 本位线性永续")
        if not quote or quote.get("source") != "okx" or not -5 <= now - quote["time"] <= 30:
            raise ValueError("OKX 行情已过期，等待恢复后重试")
        if not account or not 0 <= now - account.get("time", 0) <= 20:
            raise ValueError("账户数据已过期，等待同步后重试")
        mode = account.get("posMode")
        if account.get("acctLv") == "1":
            raise ValueError("当前账户为现货模式，无法提交永续合约订单")
        if mode not in ("net_mode", "long_short_mode"):
            raise ValueError("当前持仓模式不受支持")
        size = aligned(self.size, spec["lotSz"], "合约张数")
        if size < number(spec["minSz"]):
            raise ValueError("数量小于该合约最小下单张数")
        matching = [p for p in positions if p.get("instId") == self.instrument
                    and p.get("mgnMode") == self.margin and number(p.get("pos", "0"))]
        if self.action == "close":
            available = Decimal(0)
            for pos in matching:
                amount = number(pos["pos"])
                direction = pos.get("posSide")
                if direction == "net":
                    direction = "long" if amount > 0 else "short"
                if direction == self.direction:
                    available += abs(number(pos.get("availPos") or pos["pos"]))
            if size > available:
                raise ValueError("平仓张数超过当前可平仓数量")
        elif mode == "net_mode":
            for pos in matching:
                if (number(pos["pos"]) > 0) != (self.direction == "long"):
                    raise ValueError("当前有反向净仓，请先显式平仓")
        price = number(quote["price"], "行情", positive=True)
        payload = {"instId": self.instrument, "tdMode": self.margin, "clOrdId": client_id,
                   "side": "buy" if (self.direction == "long") == (self.action == "open") else "sell",
                   "ordType": self.order_type, "sz": str(size)}
        if mode == "long_short_mode":
            payload["posSide"] = self.direction
        elif self.action == "close":
            payload["reduceOnly"] = True
        if self.order_type == "limit":
            price = aligned(self.price, spec["tickSz"], "限价")
            payload["px"] = str(price)
        protection = {}
        if self.action == "open":
            for key, raw in (("sl", self.stop_loss), ("tp", self.take_profit)):
                if raw:
                    level = aligned(raw, spec["tickSz"], "止损" if key == "sl" else "止盈")
                    above = (self.direction == "long") == (key == "tp")
                    if (above and level <= price) or (not above and level >= price):
                        raise ValueError("止盈止损方向与入场价格不匹配")
                    protection.update({key + "TriggerPx": str(level), key + "OrdPx": "-1",
                                       key + "TriggerPxType": "last"})
            if protection:
                payload["attachAlgoOrds"] = [dict(protection, attachAlgoClOrdId=client_id + "p")]
        elif self.stop_loss or self.take_profit:
            raise ValueError("平仓订单不附带新的止盈止损")
        return payload

    def to_dict(self):
        return asdict(self)
