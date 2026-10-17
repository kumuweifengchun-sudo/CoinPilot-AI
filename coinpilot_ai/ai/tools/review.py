"""历史读取与交易归集在共享 I/O 队列执行，使用独立只读连接。"""

import json
from pathlib import Path
import sqlite3
import time
from typing import TYPE_CHECKING
from urllib.parse import quote
import uuid

from coinpilot_ai.review.journal import aggregate, summarize
from coinpilot_ai.review.manual import parse_reference, read_review
from coinpilot_ai.core.store import encode
from coinpilot_ai.review.query import JournalQuery, JournalQueryError, MAX_HISTORY_DAYS, MAX_QUERY_ROWS
from coinpilot_ai.trading.models import instrument_id
from ..models import JsonValue, validate_integer, validate_payload, validate_text
from ..transport import RequestHandle
from .base import ToolCallback, ToolContext, ToolDefinition, ToolHandler, ToolResult
from .market import INSTRUMENT, payload, schema

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


class ReviewTools:
    def __init__(self, service: "CockpitService") -> None:
        self.service = service

    def definitions(self) -> list[ToolDefinition]:
        properties: dict[str, JsonValue] = {
            "instrument": INSTRUMENT, "begin": {"type": "integer", "minimum": 0},
            "end": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "trade_id": {"type": "string", "maxLength": 200},
            "event_id": {"type": "string", "maxLength": 200},
            "report_id": {"type": "string", "maxLength": 200},
        }
        return [ToolDefinition(name, description, schema(properties), "history", self._handler(name))
                for name, description in (
                    ("get_alerts", "读取当前作用域的近期提醒事实与证据"),
                    ("get_trade_history", "读取当前作用域归集交易，保留费用和历史缺口标记"),
                    ("get_trade_review", "按交易或时间范围读取程序计算的交易统计、理由及未归属费用"),
                )]

    def _handler(self, name: str) -> ToolHandler:
        def handle(context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
            if context.scope.startswith("manual:"):
                return self.manual_handle(name, context, arguments, callback)
            now = context.captured_at
            pinned = arguments.get("event_id") or arguments.get("report_id")
            begin = validate_integer(arguments.get("begin", 0 if pinned else max(0, int(now) - 30 * 86400)), "开始时间")
            end = now if "end" not in arguments else validate_integer(arguments["end"], "结束时间", minimum=1)
            instrument = instrument_id(validate_text(arguments["instrument"], "合约", nonempty=True)) if "instrument" in arguments else None
            limit = validate_integer(arguments.get("limit", 20), "记录数量", minimum=1)
            if begin > end or end > now or limit > 50:
                raise ValueError("历史时间范围或数量无效")
            scope, path = context.scope, self.service.store.path
            def operation() -> dict[str, JsonValue]:
                uri = "file:" + quote(Path(path).resolve().as_posix(), safe="/:") + "?mode=ro"
                with sqlite3.connect(uri, uri=True) as db:
                    def records(kind: str, maximum: int) -> list[tuple[str, dict[str, JsonValue]]]:
                        sql = "SELECT id,body FROM records WHERE scope=? AND kind=? ORDER BY updated DESC,id"
                        params: list[object] = [scope, kind]
                        sql += " LIMIT ?"
                        params.append(maximum)
                        return [(key, validate_payload(json.loads(body))) for key, body in db.execute(sql, params)]
                    if name == "get_alerts":
                        result = []
                        event_rows: list[tuple[str, dict[str, JsonValue]]]
                        if arguments.get("event_id"):
                            identity = validate_text(arguments["event_id"], "事件标识", nonempty=True)
                            found = db.execute("SELECT body FROM records WHERE scope=? AND kind='event' AND id=?", (scope, identity)).fetchone()
                            event_rows = [(identity, validate_payload(json.loads(found[0])))] if found else []
                        else:
                            event_rows = records("event", 500)
                        for key, row in event_rows:
                            stamp = row.get("time", 0)
                            if not isinstance(stamp, (int, float)) or not begin <= stamp <= end:
                                continue
                            if instrument and row.get("instrument") != instrument:
                                continue
                            result.append({"id": key, **{key: row[key] for key in (
                                "name", "instrument", "time", "source", "evidence", "priority",
                            ) if key in row}})
                            if len(result) == limit:
                                break
                        return payload({"events": result})
                    if name == "get_trade_review" and arguments.get("report_id"):
                        key = validate_text(arguments["report_id"], "报告标识", nonempty=True)
                        row = db.execute("SELECT body FROM records WHERE scope=? AND kind='report' AND id=?", (scope, key)).fetchone()
                        if row is None:
                            raise ValueError("报告不存在")
                        report = validate_payload(json.loads(row[0]))
                        return payload({"report": {field: report[field] for field in ("answer", "status", "time", "service", "scene") if field in report},
                                        "boundary_note": "这是历史报告讨论，报告中的行情和账户信息不能当作当前事实"})
                    query = JournalQuery(db)
                    query_begin, query_end, query_instrument = float(begin), float(end), instrument
                    if arguments.get("trade_id"):
                        identity = validate_text(arguments["trade_id"], "交易标识", nonempty=True)
                        anchor_id, separator, direction = identity.rpartition(":")
                        if not separator or direction not in ("long", "short"):
                            raise ValueError("交易标识无效")
                        anchors = query.by_ids("fills", scope, {anchor_id})
                        if not anchors:
                            raise ValueError("交易起点记录不存在")
                        anchor = anchors[0][1]
                        anchor_time = int(str(anchor.get("fillTime") or anchor.get("ts"))) / 1000
                        anchor_instrument = instrument_id(validate_text(anchor.get("instId"), "交易合约", nonempty=True))
                        if instrument is not None and instrument != anchor_instrument:
                            raise ValueError("交易与合约不一致")
                        query_instrument = anchor_instrument
                        if "begin" not in arguments:
                            query_begin = max(0, anchor_time - 30 * 86400)
                        if "end" not in arguments:
                            query_end = min(now, query_begin + MAX_HISTORY_DAYS * 86400)
                    if query_end - query_begin > MAX_HISTORY_DAYS * 86400:
                        raise JournalQueryError("交易历史范围最多 180 天，请分段分析")
                    row_limit = max(100, limit * 40) if name == "get_trade_history" and not arguments.get("trade_id") else MAX_QUERY_ROWS
                    fills, truncated = query.window("fills", scope, int(query_begin * 1000), int(query_end * 1000),
                                                     instrument=query_instrument, limit=row_limit)
                    if truncated and name == "get_trade_review":
                        raise JournalQueryError("复盘成交记录过多，请缩小范围")
                    order_ids = {order_id for _, row in fills if isinstance(order_id := row.get("ordId"), str)}
                    orders = [row for _, row in query.by_ids("orders", scope, order_ids)]
                    trades, _ = aggregate([row for _, row in fills], (), orders)
                    selected = [trade for trade in sorted(trades, key=lambda row: row["start"], reverse=True)
                                if not arguments.get("trade_id") or trade["id"] == arguments["trade_id"]]
                    has_more = truncated or len(selected) > limit
                    if name == "get_trade_history":
                        selected = selected[:limit]
                    elif len(selected) > limit:
                        raise JournalQueryError("复盘交易过多，请缩小范围")
                    selected_ids = {trade["id"] for trade in selected}
                    selected_orders = {order_id for trade in selected for order_id in trade["order_ids"] if isinstance(order_id, str)}
                    notes = dict(query.by_ids("journal_note", scope, selected_ids))
                    local = {row.get("order_id"): row for _, row in query.by_ids("local_order", scope, selected_orders)}
                    bills = []
                    for selected_instrument in {trade["instrument"] for trade in selected}:
                        relevant = [trade for trade in selected if trade["instrument"] == selected_instrument]
                        bill_begin = min(trade["start"] for trade in relevant)
                        bill_end = max(trade["end"] or int(query_end * 1000) for trade in relevant)
                        parts, bills_truncated = query.window("bills", scope, bill_begin, bill_end,
                                                              instrument=selected_instrument)
                        bills.extend(parts)
                        if bills_truncated or len(bills) > MAX_QUERY_ROWS:
                            raise JournalQueryError("费用记录过多，请缩小范围")
                    with_funding, unassigned = aggregate([row for _, row in fills], [row for _, row in bills], orders)
                    funded = {trade["id"]: trade for trade in with_funding}
                    selected = [funded[trade["id"]] for trade in selected]
                    first_by_instrument: dict[str, str] = {}
                    for trade in with_funding:
                        first_by_instrument.setdefault(trade["instrument"], trade["id"])
                    for trade in selected:
                        if first_by_instrument.get(trade["instrument"]) == trade["id"] and query.has_earlier_fills(
                                scope, trade["instrument"], trade["start"]):
                            trade["gaps"].append("查询起点之前的仓位未知，本笔归集可能不完整")
                            trade["incomplete"], trade["status"] = True, "incomplete"
                        recorded = [local[order_id] for order_id in trade["order_ids"] if order_id in local]
                        note = notes.get(trade["id"], {})
                        if not note and recorded:
                            draft = recorded[0].get("draft")
                            if isinstance(draft, dict):
                                note = {"reason": draft.get("reason", ""), "tags": draft.get("tags", "")}
                        trade["note"] = note
                        trade["recorded_context"] = recorded
                        if len(recorded) < len(trade["order_ids"]):
                            trade["gaps"].append("部分交易缺少软件记录的理由与当时分析")
                    if name == "get_trade_history":
                        fields = ("id", "instrument", "direction", "margin", "start", "end", "opened", "closed", "net", "fees", "funding", "status", "gaps", "note")
                        return payload({"trades": [{key: trade[key] for key in fields} for trade in selected[:limit]],
                                        "has_more": has_more, "query": {"begin": query_begin, "end": query_end,
                                        "fills_read": len(fills), "truncated": truncated},
                                        "boundary_note": "仅归集查询窗口内的已知成交，开仓起点缺失时不视为完整交易"})
                    if not selected:
                        raise ValueError("没有可复盘的交易")
                    if len(selected) > limit:
                        raise ValueError("复盘范围过大，请缩小范围")
                    selected_ids = {trade["id"] for trade in selected}
                    relevant_costs = [cost for cost in unassigned if cost.get("trade") in selected_ids or
                                     any(trade["instrument"] == cost.get("bill", {}).get("instId") and
                                         trade["start"] <= int(cost["bill"]["ts"]) <= (trade["end"] or now * 1000)
                                         for trade in selected)]
                    return payload({"range": {"begin": query_begin, "end": query_end}, "trades": selected,
                                    "statistics": summarize(selected), "unassigned_costs": relevant_costs,
                                    "sync_runs": [row for _, row in records("sync_run", 5)],
                                    "boundary_note": "仅使用范围内已知成交；开仓起点可能缺失，费用与统计由程序计算，缺口不补造"})
            def done(value: object, error: object) -> None:
                message = str(error) if isinstance(error, JournalQueryError) else "历史数据不可用或范围过大，请缩小范围"
                callback(ToolResult.failure(message) if error else ToolResult(
                    True, time.time(), "coinpilot_journal", "historical", (), validate_payload(value),
                ))
            job = self.service.io.submit(operation, done)
            return RequestHandle(uuid.uuid4().hex, lambda: self.service.io.cancel(job) if job is not None else None)
        return handle

    def manual_handle(self, name: str, context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        if not context.allow_history or name not in ("get_trade_history", "get_trade_review"):
            raise ValueError("未授权手动账本历史读取")
        book_id = context.scope.removeprefix("manual:")
        now = int(context.captured_at)
        begin = validate_integer(arguments.get("begin", max(0, now - 30 * 86400)), "开始时间")
        end = validate_integer(arguments.get("end", now), "结束时间")
        limit = validate_integer(arguments.get("limit", 20), "记录数量", minimum=1)
        if not 0 <= begin <= end <= now or end - begin > MAX_HISTORY_DAYS * 86400 or limit > 50:
            raise ValueError("历史查询范围或数量无效")
        instrument = validate_text(arguments["instrument"], "交易对").upper().replace("/", "-") if arguments.get("instrument") else None
        if instrument and instrument.endswith("-SWAP"):
            instrument = instrument[:-5]
        trade_id = report_id = None
        for field in ("trade_id", "report_id"):
            if arguments.get(field):
                parsed = parse_reference(validate_text(arguments[field], "手动记录引用"))
                if parsed is None or parsed[0] != book_id:
                    raise ValueError("不能跨手动账本读取记录")
                if field == "trade_id":
                    trade_id = parsed[1]
                else:
                    report_id = parsed[1]
        path = self.service.store.path
        def operation() -> dict[str, JsonValue]:
            uri = "file:" + quote(Path(path).resolve().as_posix(), safe="/:") + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as db:
                if report_id:
                    row = db.execute("SELECT body FROM records WHERE scope=? AND kind='report' AND id=?", (context.scope, report_id)).fetchone()
                    if row is None:
                        raise ValueError("该账本中没有此报告")
                    report = validate_payload(json.loads(row[0]))
                    return payload({"report": report, "boundary_note": "历史报告与生成时快照，不代表当前账户事实"})
                result = read_review(db, book_id, begin * 1000, end * 1000, [trade_id] if trade_id else None,
                                     limit=limit, instrument=instrument)
                # 附件路径不发送给模型；截图文件没有隐式上传行为。
                clean = validate_payload(json.loads(encode(result)))
                rows = clean.get("trades")
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            row["id"] = f"manual:{book_id}:{row['id']}"
                            record = row.get("record")
                            if isinstance(record, dict):
                                record.pop("attachments", None)
                return clean
        def done(value: object, error: object) -> None:
            callback(ToolResult.failure("手动历史不可用或超出范围，请缩小范围") if error else
                     ToolResult(True, time.time(), "manual_ledger", "historical", (), validate_payload(value)))
        job = self.service.io.submit(operation, done)
        return RequestHandle(uuid.uuid4().hex, lambda: self.service.io.cancel(job) if job is not None else None)
