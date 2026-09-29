"""主图、回放与回测共享的 SQLite 数值 K 线与已收盘覆盖区间。"""
import sqlite3
import time
from pathlib import Path
import threading
from .buffer import CandleBuffer
from .history import HistoryData

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .candles import valid_candles
from coinpilot_ai.market.intervals import BARS, months, shift, floor_time, ceil_time, contiguous, can_aggregate


class MarketCache:
    COLUMNS = "stamp,open,high,low,close,volume,volume_ccy,volume_quote,confirmed"

    def __init__(self, path):
        self.path = Path(path)
        self.local = threading.local()
        self.connections = []
        self.lock = threading.Lock()

    @property
    def db(self):
        if not hasattr(self.local, "connection"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock:
                db = sqlite3.connect(self.path, check_same_thread=False)
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("PRAGMA synchronous=NORMAL")
                    db.execute("PRAGMA busy_timeout=5000")
                    self._initialize(db)
                except Exception:
                    db.close()
                    raise
                self.local.connection = db
                self.connections.append(db)
        return self.local.connection

    def _initialize(self, db):
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS candles (
                instrument TEXT NOT NULL, bar TEXT NOT NULL, stamp INTEGER NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL, volume_ccy REAL NOT NULL, volume_quote REAL NOT NULL,
                confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
                source TEXT NOT NULL, fetched REAL NOT NULL,
                PRIMARY KEY(instrument,bar,stamp)) WITHOUT ROWID""")
            db.execute("""CREATE TABLE IF NOT EXISTS candle_segments (
                instrument TEXT NOT NULL, bar TEXT NOT NULL, begin INTEGER NOT NULL,
                end INTEGER NOT NULL, source TEXT NOT NULL,
                PRIMARY KEY(instrument,bar,begin)) WITHOUT ROWID""")
            db.execute("""CREATE TABLE IF NOT EXISTS candle_aggregates (
                instrument TEXT NOT NULL, base TEXT NOT NULL, bar TEXT NOT NULL, stamp INTEGER NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL, volume_ccy REAL NOT NULL, volume_quote REAL NOT NULL,
                confirmed INTEGER NOT NULL, samples INTEGER NOT NULL,
                PRIMARY KEY(instrument,base,bar,stamp)) WITHOUT ROWID""")

    def _segment(self, instrument, bar, begin, end, source):
        step = BARS[bar]*1000
        # 相邻同源区间合并；改写来源时保留被覆盖区间的左右两侧。
        records = self.db.execute("SELECT begin,end,source FROM candle_segments "
            "WHERE instrument=? AND bar=? AND begin<=? AND end>=? ORDER BY begin",
            (instrument, bar, shift(end, bar), shift(begin, bar, -1))).fetchall()
        for left, right, old_source in records:
            if not months(bar) and (left-begin) % step:
                continue
            overlap = left <= end and right >= begin
            if old_source == source or overlap:
                self.db.execute("DELETE FROM candle_segments WHERE instrument=? AND bar=? AND begin=?",
                                (instrument, bar, left))
                if old_source == source:
                    begin, end = min(begin, left), max(end, right)
                else:
                    if left < begin:
                        self.db.execute("INSERT INTO candle_segments VALUES(?,?,?,?,?)",
                                        (instrument, bar, left, shift(begin, bar, -1), old_source))
                    if right > end:
                        self.db.execute("INSERT INTO candle_segments VALUES(?,?,?,?,?)",
                                        (instrument, bar, shift(end, bar), right, old_source))
        self.db.execute("INSERT INTO candle_segments VALUES(?,?,?,?,?)", (instrument, bar, begin, end, source))

    def _write(self, instrument, bar, rows, source, fetched):
        if bar not in BARS:
            raise ValueError("无效周期")
        changed, aggregated = [], []
        sql = ("INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
               "ON CONFLICT(instrument,bar,stamp) DO UPDATE SET "
               "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
               "volume=excluded.volume,volume_ccy=excluded.volume_ccy,volume_quote=excluded.volume_quote,"
               "confirmed=excluded.confirmed,source=excluded.source,fetched=excluded.fetched "
               "WHERE excluded.confirmed>=candles.confirmed AND "
               "(candles.open,candles.high,candles.low,candles.close,candles.volume,candles.volume_ccy,"
               "candles.volume_quote,candles.confirmed,candles.source) IS NOT "
               "(excluded.open,excluded.high,excluded.low,excluded.close,excluded.volume,excluded.volume_ccy,"
               "excluded.volume_quote,excluded.confirmed,excluded.source)")
        for row in rows:
            stamp = int(row[0])
            values = (instrument, bar, stamp, *(float(v) for v in row[1:8]), int(row[8]), source, fetched)
            if self.db.execute(sql, values).rowcount:
                aggregated.append(stamp)
                if row[8] == 1:
                    changed.append(stamp)
        step = BARS[bar]*1000
        begin = end = None
        for stamp in changed:
            if end is not None and not contiguous(end, stamp, bar):
                self._segment(instrument, bar, begin, end, source)
                begin = None
            if begin is None:
                begin = stamp
            end = stamp
        if begin is not None:
            self._segment(instrument, bar, begin, end, source)

        return aggregated

    def put(self, instrument, bar, rows, source="okx"):
        clean = valid_candles(rows)
        with self.db:
            changed = self._write(instrument, bar, clean, source, time.time())
            self._aggregate(instrument, bar, changed)
        return len(clean)

    def range(self, instrument, bar, begin, end):
        rows = self.db.execute(f"SELECT {self.COLUMNS} FROM candles WHERE instrument=? AND bar=? "
                               "AND stamp BETWEEN ? AND ? ORDER BY stamp", (instrument, bar, int(begin), int(end)))
        return CandleBuffer(rows)

    def tail(self, instrument, bar, limit=1000, *, before=None):
        boundary = " AND stamp<?" if before is not None else ""
        params = (instrument, bar, int(before), max(1, int(limit))) if before is not None else (
            instrument, bar, max(1, int(limit)))
        rows = self.db.execute(f"SELECT {self.COLUMNS} FROM candles WHERE instrument=? AND bar=?" +
                               boundary + " ORDER BY stamp DESC LIMIT ?", params).fetchall()
        return CandleBuffer(reversed(rows))

    def dataset(self, instrument, bar, begin, end, *, cancelled=lambda: False):
        cursor = self.db.execute(f"SELECT {self.COLUMNS} FROM candles WHERE instrument=? AND bar=? "
                                 "AND stamp BETWEEN ? AND ? ORDER BY stamp", (instrument, bar, int(begin), int(end)))
        return HistoryData.create(cursor, BARS[bar]*1000, bar=bar, cancelled=cancelled)

    def _aggregate(self, instrument, base, stamps):
        step = BARS[base]*1000
        for bar, seconds in BARS.items():
            interval = seconds*1000
            if not can_aggregate(base, bar):
                continue
            for begin in {floor_time(stamp, bar) for stamp in stamps}:
                end = shift(begin, bar)
                records = self.db.execute(f"SELECT {self.COLUMNS} FROM candles WHERE instrument=? AND bar=? "
                    "AND stamp>=? AND stamp<? ORDER BY stamp", (instrument, base, begin, end)).fetchall()
                if not records:
                    continue
                complete = (all(row[8] for row in records) and records[0][0] == begin
                            and shift(records[-1][0], base) == end
                            and all(contiguous(a[0], b[0], base) for a, b in zip(records, records[1:])))
                row = (begin, records[0][1], max(r[2] for r in records), min(r[3] for r in records), records[-1][4],
                       *(sum(r[i] for r in records) for i in (5,6,7)), int(complete))
                self.db.execute("INSERT OR REPLACE INTO candle_aggregates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (instrument, base, bar, *row, len(records)))

    def aggregate_range(self, instrument, base, bar, begin, end):
        if base == bar:
            return self.range(instrument, bar, begin, end)
        rows = self.db.execute(f"SELECT {self.COLUMNS} FROM candle_aggregates WHERE instrument=? AND base=? "
                              "AND bar=? AND stamp BETWEEN ? AND ? AND confirmed=1 ORDER BY stamp",
                              (instrument, base, bar, int(begin), int(end)))
        return CandleBuffer(rows)

    def gap_ranges(self, instrument, bar, begin, end):
        """返回缺失区间，内存与区间数相关；支持 OKX 日线的非 UTC 零点偏移。"""
        if bar not in BARS:
            raise ValueError("无效周期")
        step = BARS[bar]*1000
        row = self.db.execute("SELECT begin FROM candle_segments WHERE instrument=? AND bar=? LIMIT 1",
                              (instrument, bar)).fetchone()
        anchor = row[0] if row else None
        expected = ceil_time(begin, bar, anchor=anchor)
        end = int(end)
        cursor = self.db.execute("SELECT begin,end FROM candle_segments WHERE instrument=? AND bar=? "
                                 "AND begin<=? AND end>=? ORDER BY begin",
                                 (instrument, bar, end, expected))
        for left, right in cursor:
            if not months(bar) and (left-expected) % step:
                continue
            if left > expected:
                yield expected, min(end, shift(left, bar, -1))
            expected = max(expected, shift(right, bar))
            if expected > end:
                return
        if expected <= end:
            yield expected, end

    def first_gap(self, instrument, bar, begin, end):
        return next((left for left, _ in self.gap_ranges(instrument, bar, begin, end)), None)

    def has_range(self, instrument, bar, begin, end):
        return self.first_gap(instrument, bar, begin, end) is None

    def coverage(self, instrument, bar, begin, end):
        records = self.db.execute("SELECT begin,end,source FROM candle_segments WHERE instrument=? AND bar=? "
                                  "AND begin<=? AND end>=? ORDER BY begin", (instrument, bar, int(end), int(begin)))
        step = BARS[bar]*1000
        segments = []
        for left, right, source in records:
            start = max(left, ceil_time(begin, bar, anchor=left))
            stop = min(right, floor_time(end, bar, anchor=right))
            if start <= stop:
                segments.append({"begin": start, "end": stop, "source": source})
        return {"segments": segments, "gaps": list(self.gap_ranges(instrument, bar, begin, end))}

    def close(self):
        for db in self.connections:
            db.close()
        self.connections.clear()


class HistoryLoader(QObject):
    finished = pyqtSignal(object, object)

    def __init__(self, service, cache, parent=None):
        super().__init__(parent)
        self.service, self.cache = service, cache
        self.generation = 0
        self.active = False
        self.job = None

    def cancel(self):
        self.generation += 1
        self.active = False
        if self.job is not None:
            self.service.io.cancel(self.job)
        self.job = None

    def load(self, instrument, bar, begin, end):
        if bar not in BARS or begin >= end:
            raise ValueError("回放时间范围无效")
        self.cancel()
        self.active = True
        generation = self.generation
        step = BARS[bar]*1000
        visited = set()
        def alive():
            return generation == self.generation and not self.service.closed
        def fail(error):
            if alive():
                self.active = False
                self.finished.emit(None, str(error))
        def ready(data, error):
            if not alive():
                if data is not None:
                    data.close()
                return
            self.active = False
            self.finished.emit(data, str(error) if error else None)
        def checked(gap, error):
            if not alive():
                return
            if error:
                fail(error)
                return
            if gap is None:
                self.job = self.service.io.submit(lambda: self.cache.dataset(instrument, bar, begin, end, cancelled=lambda: not alive()), ready)
                return
            cursor = min(shift(floor_time(end, bar, anchor=gap), bar), shift(gap, bar, 300))
            if cursor in visited:
                fail("历史分页未前进，数据仍存在缺口")
                return
            visited.add(cursor)
            def received(rows, error):
                if not alive():
                    return
                if error or not rows:
                    fail(error or "已到数据源历史边界")
                    return
                def store():
                    self.cache.put(instrument, bar, rows)
                    return self.cache.first_gap(instrument, bar, begin, end)
                def next_page(value, error):
                    if alive():
                        QTimer.singleShot(350, lambda: checked(value, error))
                self.job = self.service.io.submit(store, next_page)
            self.service.api.get("/api/v5/market/history-candles", received,
                                 {"instId": instrument, "bar": bar, "after": str(cursor), "limit": "300"})
        self.job = self.service.io.submit(lambda: self.cache.first_gap(instrument, bar, begin, end), checked)
