"""从已收盘 K 线截取价格走势，并按目标价格投影。"""
import math


MAX_PATTERN_POINTS = 500


def capture_pattern(rows, interval):
    """返回源时间与收盘价快照；不允许用缺口或未收盘数据补造走势。"""
    if not 2 <= len(rows) <= MAX_PATTERN_POINTS:
        raise ValueError(f"请选择 2—{MAX_PATTERN_POINTS} 根 K 线")
    stamps, closes = [], []
    for row in rows:
        if len(row) < 9 or str(row[8]) != "1":
            raise ValueError("走势复制只支持已收盘 K 线")
        stamp, close = int(row[0]), float(row[4])
        if not math.isfinite(close) or close <= 0:
            raise ValueError("源区间存在无效收盘价")
        if stamps and stamp - stamps[-1] != interval:
            raise ValueError("源区间存在 K 线缺口")
        stamps.append(stamp)
        closes.append(close)
    return stamps[0], stamps[-1], closes


def projected_prices(closes, anchor_price):
    """让源走势首点落在目标价格，保留其百分比变化。"""
    if not closes or closes[0] <= 0 or not math.isfinite(anchor_price) or anchor_price <= 0:
        raise ValueError("走势锚定价格无效")
    prices = [anchor_price * close / closes[0] for close in closes]
    if any(not math.isfinite(price) or price <= 0 for price in prices):
        raise ValueError("走势投影价格无效")
    return prices
