"""OKX 交易产品 K 线周期；BARS 的月秒数仅用于视口跨度估计。

收盘、连续性、分页与聚合必须使用 shift/floor_time，不能用月秒数相加。
来源：https://www.okx.com/docs-v5/zh/ （市场数据 REST 与 K 线 WS 频道）。
"""
from datetime import datetime, timezone

_SHORT = {"1s": 1, "1m": 60, "3m": 180, "5m": 300, "15m": 900,
          "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400}
_LONG = {"6H": 21600, "12H": 43200, "1D": 86400, "2D": 172800,
         "3D": 259200, "5D": 432000, "1W": 604800,
         "1M": 30*86400, "3M": 90*86400}
BARS = {**_SHORT, **_LONG, **{bar+"utc": seconds for bar, seconds in _LONG.items()}}
QUICK_BARS = ("1m", "5m", "15m", "1H", "4H", "1D", "1W", "1M")


def months(bar):
    name = bar.removesuffix("utc")
    return int(name[:-1]) if name.endswith("M") else 0


def offset_ms(bar):
    return 0 if bar.endswith("utc") or bar in _SHORT else 8*3600000


def shift(stamp, bar, count=1):
    """移动整数根 K 线；输入为开盘时间戳（毫秒）。"""
    count = int(count)
    size = months(bar)
    if not size:
        return int(stamp) + count*BARS[bar]*1000
    offset = offset_ms(bar)
    date = datetime.fromtimestamp((stamp+offset)/1000, timezone.utc)
    index = date.year*12+date.month-1+count*size
    year, month = divmod(index, 12)
    return int(datetime(year, month+1, 1, tzinfo=timezone.utc).timestamp()*1000)-offset


def floor_time(stamp, bar, *, anchor=None):
    size, offset = months(bar), offset_ms(bar)
    if size:
        date = datetime.fromtimestamp((stamp+offset)/1000, timezone.utc)
        month = (date.month-1)//size*size+1
        return int(datetime(date.year, month, 1, tzinfo=timezone.utc).timestamp()*1000)-offset
    step = BARS[bar]*1000
    # 周线从周一开始；多日线与交易所 Unix 日序对齐。
    origin = (4*86400000 if bar.removesuffix("utc") == "1W" else 0)-offset
    if anchor is not None:
        origin = int(anchor)
    return int((stamp-origin)//step)*step+origin


def ceil_time(stamp, bar, *, anchor=None):
    floor = floor_time(stamp, bar, anchor=anchor)
    return shift(floor, bar) if floor < stamp else floor


def openings(begin, end, bar, *, anchor=None):
    stamp = ceil_time(begin, bar, anchor=anchor)
    while stamp <= end:
        yield stamp
        stamp = shift(stamp, bar)


def contiguous(previous, current, bar):
    return int(current) == shift(int(previous), bar)


def can_aggregate(base, bar):
    """仅允许完整包含源 K 线的周期组合，避免跨时区/跨周月切分。"""
    if BARS[bar] <= BARS[base]:
        return False
    if months(base):
        return bool(months(bar) and months(bar) % months(base) == 0
                    and offset_ms(base) == offset_ms(bar))
    if months(bar) or bar.removesuffix("utc") == "1W":
        return (BARS[base] <= 86400 and 86400 % BARS[base] == 0
                and (offset_ms(base)-offset_ms(bar)) % (BARS[base]*1000) == 0)
    return (BARS[bar] % BARS[base] == 0
            and (offset_ms(base)-offset_ms(bar)) % (BARS[base]*1000) == 0)


def display_candidates(base):
    utc = base.endswith("utc")
    return sorted((bar for bar in BARS if bar == base or
                   (bar.endswith("utc") == utc and can_aggregate(base, bar))), key=BARS.__getitem__)
