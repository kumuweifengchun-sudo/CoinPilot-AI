"""时间跨度决定数据周期，像素桶保留 OHLC 极值与成交量。"""
from .intervals import BARS, display_candidates, contiguous
from .buffer import CandleBuffer

MAX_VIEW_BARS = 5_256_000  # 十年分钟线，可继续通过更长基础周期查看。


def display_bar(base, count, width):
    seconds = count*BARS[base]
    budget = max(200, int(width*1.5))
    eligible = display_candidates(base)
    return next((bar for bar in eligible if seconds/BARS[bar] <= budget), eligible[-1])


def pixel_buckets(rows, left, span, width, interval, *, bar=None):
    result, group, key, last = CandleBuffer(), None, None, None
    for row in rows:
        bucket = int((row[0]-left)/span*max(1, int(width)))
        if bucket != key or last is None or (not contiguous(last, row[0], bar) if bar else row[0]-last != interval):
            if group is not None:
                result.append(tuple(group))
            group, key = list(row), bucket
        else:
            assert group is not None
            group[2], group[3], group[4] = max(group[2], row[2]), min(group[3], row[3]), row[4]
            for i in (5, 6, 7):
                group[i] += row[i]
            group[8] = min(group[8], row[8])
        last = row[0]
    if group is not None:
        result.append(tuple(group))
    return result
