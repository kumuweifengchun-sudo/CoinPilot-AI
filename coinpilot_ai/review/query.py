"""有索引、范围与数量上限的日志读取；保持归集规则由 journal.aggregate 负责。"""

import json
import sqlite3

type JournalRow = dict[str, object]

FILL_TIME = "CAST(COALESCE(NULLIF(json_extract(body,'$.fillTime'),''),json_extract(body,'$.ts')) AS INTEGER)"
BILL_TIME = "CAST(json_extract(body,'$.ts') AS INTEGER)"
MAX_HISTORY_DAYS = 180
MAX_QUERY_ROWS = 5000
MAX_QUERY_BYTES = 8 * 1024 * 1024


class JournalQueryError(ValueError):
    """只用于可向用户显示的固定范围／数据缺口消息。"""


def ensure_journal_indexes(db: sqlite3.Connection) -> bool:
    """旧 SQLite 缺少 JSON 时保持主业务可用，依赖 JSON 的工具会明确失败。"""
    try:
        if db.execute("SELECT json_extract('{\"a\":1}','$.a')").fetchone()[0] != 1:
            return False
    except sqlite3.OperationalError:
        return False
    with db:
        db.execute("CREATE INDEX IF NOT EXISTS manual_trade_review_time ON records(scope,json_extract(body,'$.review_time'),id) "
                   "WHERE kind='manual_trade' AND json_valid(body)")
        for kind, stamp in (("fills", FILL_TIME), ("bills", BILL_TIME)):
            db.execute(f"CREATE INDEX IF NOT EXISTS journal_{kind}_time ON records(scope,{stamp},id) "
                       f"WHERE kind='{kind}' AND json_valid(body)")
            db.execute(f"CREATE INDEX IF NOT EXISTS journal_{kind}_instrument_time "
                       f"ON records(scope,json_extract(body,'$.instId'),{stamp},id) "
                       f"WHERE kind='{kind}' AND json_valid(body)")
        db.execute("CREATE INDEX IF NOT EXISTS journal_local_order ON records(scope,json_extract(body,'$.order_id')) "
                   "WHERE kind='local_order' AND json_valid(body)")
    return True


class JournalQuery:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        try:
            if db.execute("SELECT json_extract('{\"a\":1}','$.a')").fetchone()[0] != 1:
                raise JournalQueryError("当前 SQLite 不支持日志 JSON 查询")
        except sqlite3.OperationalError:
            raise JournalQueryError("当前 SQLite 不支持日志 JSON 查询") from None
        self.rows_read = 0
        self.bytes_read = 0

    def _rows(self, cursor: sqlite3.Cursor) -> list[tuple[str, JournalRow]]:
        rows: list[tuple[str, JournalRow]] = []
        for key, body in cursor:
            if not isinstance(key, str) or not isinstance(body, str):
                raise ValueError("日志记录格式无效")
            self.rows_read += 1
            self.bytes_read += len(body.encode())
            if self.bytes_read > MAX_QUERY_BYTES:
                raise JournalQueryError("日志数据过大，请缩小范围")
            value: object = json.loads(body)
            if not isinstance(value, dict) or any(not isinstance(name, str) for name in value):
                raise ValueError("日志内容必须是对象")
            rows.append((key, {name: item for name, item in value.items()}))
        return rows

    def window(self, kind: str, scope: str, begin_ms: int, end_ms: int, *,
               instrument: str | None = None, limit: int = MAX_QUERY_ROWS) -> tuple[list[tuple[str, JournalRow]], bool]:
        if kind not in ("fills", "bills") or not 1 <= limit <= MAX_QUERY_ROWS:
            raise ValueError("日志查询类型或数量无效")
        if begin_ms < 0 or begin_ms > end_ms or end_ms - begin_ms > MAX_HISTORY_DAYS * 86400000:
            raise JournalQueryError("交易历史范围最多 180 天，请分段分析")
        stamp = FILL_TIME if kind == "fills" else BILL_TIME
        sql = f"SELECT id,body FROM records WHERE scope=? AND kind='{kind}' AND json_valid(body)"
        parameters: list[object] = [scope]
        if instrument is not None:
            sql += " AND json_extract(body,'$.instId')=?"
            parameters.append(instrument)
        sql += f" AND {stamp} BETWEEN ? AND ? ORDER BY {stamp} DESC,id DESC LIMIT ?"
        parameters.extend((begin_ms, end_ms, limit + 1))
        rows = self._rows(self.db.execute(sql, parameters))
        return rows[:limit], len(rows) > limit

    def by_ids(self, kind: str, scope: str, identities: set[str]) -> list[tuple[str, JournalRow]]:
        if kind not in ("orders", "journal_note", "local_order", "fills") or len(identities) > MAX_QUERY_ROWS:
            raise ValueError("日志引用数量或类型无效")
        result: list[tuple[str, JournalRow]] = []
        values = sorted(identities)
        field = "json_extract(body,'$.order_id')" if kind == "local_order" else "id"
        for offset in range(0, len(values), 200):
            batch = values[offset:offset + 200]
            placeholders = ",".join("?" for _ in batch)
            cursor = self.db.execute(f"SELECT id,body FROM records WHERE scope=? AND kind='{kind}' "
                                     f"AND json_valid(body) AND {field} IN ({placeholders}) LIMIT ?",
                                     [scope, *batch, MAX_QUERY_ROWS + 1])
            result.extend(self._rows(cursor))
            if len(result) > MAX_QUERY_ROWS:
                raise JournalQueryError("关联日志过多，请缩小范围")
        return result

    def has_earlier_fills(self, scope: str, instrument: str, before_ms: int) -> bool:
        return self.db.execute(f"SELECT 1 FROM records WHERE scope=? AND kind='fills' AND json_valid(body) "
                               f"AND json_extract(body,'$.instId')=? AND {FILL_TIME}<? LIMIT 1",
                               (scope, instrument, before_ms)).fetchone() is not None
