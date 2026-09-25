"""成交归集与可核验统计；未知成本和缺失开仓不会被伪造成完整交易。"""
from decimal import Decimal

from coinpilot_ai.trading.models import number


def aggregate(fills, bills=(), orders=()):
    order_map = {row.get("ordId"): row for row in orders}
    active, cycles, unassigned, seen = {}, [], [], set()
    zero = Decimal(0)

    def new(fill, direction, mode, incomplete=False):
        row = {"id": str(fill.get("billId") or fill.get("tradeId")) + ":" + direction,
               "instrument": fill["instId"], "direction": direction, "margin": mode,
               "start": int(fill.get("fillTime") or fill["ts"]), "end": None,
               "quantity": zero, "opened": zero, "closed": zero, "realized": zero,
               "fees": zero, "funding": zero, "fills": [], "order_ids": [], "gaps": [],
               "incomplete": incomplete or mode == "unknown"}
        if mode == "unknown":
            row["gaps"].append("缺少订单保证金模式，归集可能无法区分逐仓与全仓")
        if incomplete:
            row["gaps"].append("缺少开始持仓或开仓成交记录")
        cycles.append(row)
        return row

    def apply(row, fill, size, closing, ratio, pnl):
        row["fills"].append(dict(fill, allocatedSz=str(size)))
        if fill.get("ordId") not in row["order_ids"]:
            row["order_ids"].append(fill.get("ordId"))
        row["closed" if closing else "opened"] += size
        row["quantity"] += -size if closing else size
        if fill.get("fillPnl") in (None, ""):
            row["incomplete"] = True
            if "部分成交缺少已实现盈亏" not in row["gaps"]:
                row["gaps"].append("部分成交缺少已实现盈亏")
        row["realized"] += pnl
        fee = number(fill.get("fee") or "0") * ratio
        if fill.get("feeCcy") == "USDT":
            row["fees"] += fee
        elif fee:
            unassigned.append({"kind": "fee", "amount": str(fee), "currency": fill.get("feeCcy"),
                               "trade": row["id"], "reason": "非 USDT 费用未折算"})
        if closing and row["quantity"] <= 0:
            row["end"] = int(fill.get("fillTime") or fill["ts"])

    sorted_fills = sorted(fills, key=lambda f: (int(f.get("fillTime") or f["ts"]),
                                               int(f.get("billId") or f.get("tradeId") or 0)))
    for fill in sorted_fills:
        inst = fill.get("instId", "")
        identity = (inst, fill.get("billId") or fill.get("tradeId"))
        if not inst.endswith("-USDT-SWAP") or identity in seen:
            continue
        seen.add(identity)
        size = number(fill["fillSz"], positive=True)
        pnl = number(fill.get("fillPnl") or "0")
        mode = order_map.get(fill.get("ordId"), {}).get("tdMode") or fill.get("mgnMode") or "unknown"
        side, pos_side = fill["side"], fill.get("posSide", "net")
        if pos_side in ("long", "short"):
            direction = pos_side
            closing = (direction == "long") != (side == "buy")
            key = (inst, mode, direction)
            row = active.get(key)
            if row is None:
                row = new(fill, direction, mode, closing)
                active[key] = row
                if closing:
                    row["quantity"] = size
            if closing and size > row["quantity"]:
                row["incomplete"] = True
                row["gaps"].append("平仓数量超过已知开仓数量")
                row["quantity"] = size
            apply(row, fill, size, closing, Decimal(1), pnl)
            if row["end"] is not None:
                active.pop(key, None)
            continue
        key = (inst, mode, "net")
        row = active.get(key)
        direction = "long" if side == "buy" else "short"
        # 明确的平仓 subtype 可以识别历史截断后的第一条成交。
        explicit_close = str(fill.get("subType")) in ("5", "6", "100", "101", "102", "103", "104", "105")
        if row is None and explicit_close:
            row = new(fill, "short" if side == "buy" else "long", mode, True)
            row["quantity"] = size
            apply(row, fill, size, True, Decimal(1), pnl)
            continue
        if row is None:
            row = active[key] = new(fill, direction, mode)
        if row["direction"] == direction:
            apply(row, fill, size, False, Decimal(1), pnl)
        else:
            closed = min(row["quantity"], size)
            apply(row, fill, closed, True, closed / size, pnl)
            remainder = size - closed
            if row["end"] is not None:
                active.pop(key, None)
            if remainder:
                row = active[key] = new(fill, direction, mode)
                apply(row, fill, remainder, False, remainder / size, zero)

    bill_seen = set()
    for bill in bills:
        if str(bill.get("type")) != "8" or bill.get("billId") in bill_seen:
            continue
        bill_seen.add(bill.get("billId"))
        stamp = int(bill["ts"])
        candidates = [row for row in cycles if row["instrument"] == bill.get("instId")
                      and row["start"] <= stamp <= (row["end"] or float("inf"))
                      and (not bill.get("mgnMode") or row["margin"] == bill["mgnMode"])
                      and (bill.get("posSide") not in ("long", "short") or row["direction"] == bill["posSide"])]
        amount = number(bill.get("balChg") or bill.get("pnl") or "0")
        if len(candidates) == 1 and bill.get("ccy") == "USDT":
            candidates[0]["funding"] += amount
        else:
            unassigned.append({"kind": "funding", "bill": bill, "amount": str(amount),
                               "currency": bill.get("ccy"), "reason": "无法唯一归属到交易"})
    for row in cycles:
        row["net"] = row["realized"] + row["fees"] + row["funding"]
        row["status"] = "incomplete" if row["incomplete"] else "closed" if row["end"] else "open"
        row["gaps"] = list(dict.fromkeys(row["gaps"]))
    return cycles, unassigned


def summarize(trades):
    complete = [row for row in trades if row["status"] == "closed"]
    return {"selected": len(trades), "complete_closed": len(complete),
            "open_or_incomplete": len(trades) - len(complete),
            "realized": str(sum((number(row["realized"]) for row in trades), Decimal(0))),
            "fees": str(sum((number(row["fees"]) for row in trades), Decimal(0))),
            "funding": str(sum((number(row["funding"]) for row in trades), Decimal(0))),
            "known_net": str(sum((number(row["net"]) for row in trades), Decimal(0))),
            "wins_complete": sum(number(row["net"]) > 0 for row in complete),
            "note": "金额为已同步记录中可核验的 USDT 数值；已实现收益不含未实现盈亏，缺失和未归属费用单独列示。"}
