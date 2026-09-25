"""只对完整已平仓交易计算可核验的交易统计。"""
from decimal import Decimal

from coinpilot_ai.trading.models import number


def statistics(trades):
    closed = sorted((row for row in trades if row.get("status") == "closed"),
                    key=lambda row: (row.get("end") or 0, row["id"]))
    wins = losses = Decimal(0)
    equity = peak = drawdown = Decimal(0)
    r_values = []
    for row in closed:
        net = number(row["net"])
        wins += max(net, 0)
        losses += max(-net, 0)
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
        risk = row.get("note", {}).get("risk_budget")
        if risk not in (None, ""):
            value = number(risk)
            if value > 0:
                r_values.append(net/value)
    return {"trades": len(closed), "excluded": len(trades)-len(closed),
            "win_rate": (sum(number(row["net"]) > 0 for row in closed)/len(closed) if closed else None),
            "profit_factor": wins/losses if losses else None,
            "average_r": sum(r_values, Decimal(0))/len(r_values) if r_values else None,
            "r_samples": len(r_values), "max_drawdown": drawdown, "known_net": equity}


def grouped_statistics(trades, field):
    if field not in ("direction", "instrument", "tag"):
        raise ValueError("不支持的分组字段")
    groups = {}
    for row in trades:
        if field == "tag":
            values = row.get("note", {}).get("tags", []) or ["未标记"]
        else:
            values = [row.get(field, "未知")]
        for value in values:
            groups.setdefault(str(value), []).append(row)
    return {name: statistics(rows) for name, rows in sorted(groups.items())}
