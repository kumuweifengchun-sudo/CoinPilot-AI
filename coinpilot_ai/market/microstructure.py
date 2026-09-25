"""盘口序列、逐笔 CVD 与基于真实成交价的 Volume Profile。"""
from collections import deque
from decimal import Decimal, ROUND_FLOOR

from coinpilot_ai.trading.models import number


class SequenceGap(ValueError):
    pass


class OrderBook:
    def __init__(self):
        self.bids, self.asks = {}, {}
        self.sequence = None
        self.time = 0

    def reset(self):
        self.bids.clear()
        self.asks.clear()
        self.sequence = None
        self.time = 0

    def apply(self, action, data):
        sequence = int(data["seqId"])
        if action == "snapshot":
            self.reset()
        elif self.sequence is None or int(data["prevSeqId"]) != self.sequence:
            self.reset()
            raise SequenceGap("盘口增量序列断裂，需要重新获取快照")
        for field, target in (("bids", self.bids), ("asks", self.asks)):
            for row in data.get(field, []):
                price = number(row[0], positive=True)
                size = number(row[1])
                if size < 0:
                    raise ValueError("盘口数量无效")
                if size:
                    target[price] = size
                else:
                    target.pop(price, None)
        self.sequence = sequence
        self.time = int(data["ts"])/1000

    def top(self, limit=10):
        return (sorted(self.asks.items())[:limit], sorted(self.bids.items(), reverse=True)[:limit])


class TradeTape:
    def __init__(self, limit=10000):
        self.trades = deque(maxlen=limit)
        self.seen = set()
        self.cvd = Decimal(0)
        self.last_time = 0
        self.direction_available = True

    def reset(self):
        self.trades.clear()
        self.seen.clear()
        self.cvd = Decimal(0)
        self.last_time = 0
        self.direction_available = True

    def ingest(self, rows):
        changed = False
        for row in sorted(rows, key=lambda item: (int(item["ts"]), str(item.get("tradeId", "")))):
            identity = str(row.get("tradeId", ""))
            stamp = int(row["ts"])
            if not identity or identity in self.seen or stamp < self.last_time:
                continue
            side = row.get("side")
            if side not in ("buy", "sell"):
                self.direction_available = False
                continue
            trade = {"id": identity, "time": stamp, "price": number(row["px"], positive=True),
                     "size": number(row["sz"], positive=True), "side": side}
            if len(self.trades) == self.trades.maxlen:
                self.seen.discard(self.trades[0]["id"])
            self.trades.append(trade)
            self.seen.add(identity)
            self.last_time = stamp
            self.cvd += trade["size"] if side == "buy" else -trade["size"]
            changed = True
        return changed


def volume_profile(trades, tick_size, *, begin=None, end=None, value_area=Decimal("0.70")):
    step = number(tick_size, positive=True)
    fraction = number(value_area, positive=True)
    if fraction > 1:
        raise ValueError("价值区域比例不能超过 100%")
    bins = {}
    for trade in trades:
        stamp = int(trade["time"])
        if begin is not None and stamp < begin or end is not None and stamp > end:
            continue
        price = number(trade["price"], positive=True)
        size = number(trade["size"], positive=True)
        level = (price/step).to_integral_value(rounding=ROUND_FLOOR)*step
        bins[level] = bins.get(level, Decimal(0))+size
    if not bins:
        return None
    levels = sorted(bins)
    poc = max(levels, key=lambda level: (bins[level], -level))
    total = sum(bins.values(), Decimal(0))
    left = right = levels.index(poc)
    included = bins[poc]
    while included < total*fraction and (left > 0 or right < len(levels)-1):
        down = bins[levels[left-1]] if left > 0 else Decimal(-1)
        up = bins[levels[right+1]] if right < len(levels)-1 else Decimal(-1)
        if up >= down:
            right += 1
            included += up
        else:
            left -= 1
            included += down
    return {"bins": bins, "poc": poc, "val": levels[left], "vah": levels[right],
            "total": total, "covered": included/total}
