"""只对完整已平仓交易计算可核验的交易统计。"""
from decimal import Decimal
from collections.abc import Mapping, Sequence
from typing import TypedDict

from coinpilot_ai.trading.models import number


class Statistics(TypedDict):
    trades: int
    excluded: int
    wins: int
    losses: int
    breakeven: int
    average_win: Decimal | None
    average_loss: Decimal | None
    average_duration_ms: float | None
    win_rate: float | None
    profit_factor: Decimal | None
    average_r: Decimal | None
    r_samples: int
    max_drawdown: Decimal
    known_net: Decimal


def statistics(trades: Sequence[Mapping[str, object]]) -> Statistics:
    closed = sorted((row for row in trades if row.get("status") == "closed"),
                    key=lambda row: (number(row.get("end") or 0), str(row["id"])))
    wins = losses = Decimal(0)
    equity = peak = drawdown = Decimal(0)
    r_values: list[Decimal] = []
    for row in closed:
        net = number(row["net"])
        wins += max(net, 0)
        losses += max(-net, 0)
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
        note = row.get("note", {})
        risk = note.get("risk_budget") if isinstance(note, dict) else None
        if risk not in (None, ""):
            value = number(risk)
            if value > 0:
                r_values.append(net/value)
    return {"trades": len(closed), "excluded": len(trades)-len(closed),
            "wins": sum(number(row["net"]) > 0 for row in closed),
            "losses": sum(number(row["net"]) < 0 for row in closed),
            "breakeven": sum(number(row["net"]) == 0 for row in closed),
            "average_win": wins / count if (count := sum(number(row["net"]) > 0 for row in closed)) else None,
            "average_loss": -losses / count if (count := sum(number(row["net"]) < 0 for row in closed)) else None,
            "average_duration_ms": float(sum((number(row["end"]) - number(row["start"]) for row in closed), Decimal(0)) / len(closed)) if closed else None,
            "win_rate": (sum(number(row["net"]) > 0 for row in closed)/len(closed) if closed else None),
            "profit_factor": wins/losses if losses else None,
            "average_r": sum(r_values, Decimal(0))/len(r_values) if r_values else None,
            "r_samples": len(r_values), "max_drawdown": drawdown, "known_net": equity}


def grouped_statistics(trades: Sequence[Mapping[str, object]], field: str) -> dict[str, Statistics]:
    if field not in ("direction", "instrument", "tag", "product"):
        raise ValueError("不支持的分组字段")
    groups: dict[str, list[Mapping[str, object]]] = {}
    for row in trades:
        if field == "tag":
            note = row.get("note", {})
            tags = note.get("tags", []) if isinstance(note, dict) else []
            values = tags if isinstance(tags, list) and tags else ["未标记"]
        else:
            values = [row.get(field, "未知")]
        for value in values:
            groups.setdefault(str(value), []).append(row)
    return {name: statistics(rows) for name, rows in sorted(groups.items())}
