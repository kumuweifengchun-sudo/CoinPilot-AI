"""独立 SQLite K 线缓存，供回放与回测使用。"""
import json
import sqlite3
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .candles import valid_candles
from coinpilot_ai.market.intervals import BARS


class MarketCache:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS candles (
            instrument TEXT NOT NULL, bar TEXT NOT NULL, stamp INTEGER NOT NULL,
            body TEXT NOT NULL, source TEXT NOT NULL, fetched REAL NOT NULL,
            PRIMARY KEY(instrument,bar,stamp))""")

    def put(self, instrument, bar, rows, source="okx"):
        if bar not in BARS:
            raise ValueError("无效周期")
        clean = valid_candles(rows)
        now = time.time()
        with self.db:
            self.db.executemany("INSERT INTO candles VALUES(?,?,?,?,?,?) ON CONFLICT(instrument,bar,stamp) "
                                "DO UPDATE SET body=excluded.body,source=excluded.source,fetched=excluded.fetched",
                                [(instrument, bar, int(row[0]), json.dumps(row), source, now) for row in clean])
        return len(clean)

    def range(self, instrument, bar, begin, end):
        rows = self.db.execute("SELECT body FROM candles WHERE instrument=? AND bar=? AND stamp BETWEEN ? AND ? "
                               "ORDER BY stamp", (instrument, bar, int(begin), int(end))).fetchall()
        return [json.loads(body) for (body,) in rows]

    def gaps(self, instrument, bar, begin, end):
        step = BARS[bar]*1000
        stamps = {int(row[0]) for row in self.range(instrument, bar, begin, end)}
        first = ((int(begin)+step-1)//step)*step
        return [stamp for stamp in range(first, int(end)+1, step) if stamp not in stamps]

    def coverage(self, instrument, bar, begin, end):
        """按连续时间和数据来源汇总已缓存区间。"""
        if bar not in BARS:
            raise ValueError("无效周期")
        records = self.db.execute("SELECT stamp,source FROM candles WHERE instrument=? AND bar=? "
                                  "AND stamp BETWEEN ? AND ? ORDER BY stamp",
                                  (instrument, bar, int(begin), int(end))).fetchall()
        segments = []
        step = BARS[bar]*1000
        for stamp, source in records:
            if segments and segments[-1]["source"] == source and stamp-segments[-1]["end"] == step:
                segments[-1]["end"] = stamp
            else:
                segments.append({"begin": stamp, "end": stamp, "source": source})
        return {"segments": segments, "gaps": self.gaps(instrument, bar, begin, end)}

    def close(self):
        self.db.close()


class HistoryLoader(QObject):
    finished = pyqtSignal(object, object)

    def __init__(self, service, cache, parent=None):
        super().__init__(parent)
        self.service, self.cache = service, cache
        self.generation = 0
        self.active = False

    def cancel(self):
        self.generation += 1
        self.active = False

    def load(self, instrument, bar, begin, end):
        if bar not in BARS or begin >= end:
            raise ValueError("回放时间范围无效")
        self.cancel()
        self.active = True
        generation = self.generation
        missing = self.cache.gaps(instrument, bar, begin, end)
        if not missing:
            self.active = False
            self.finished.emit(self.cache.range(instrument, bar, begin, end), None)
            return
        cursor = int(end) + BARS[bar]*1000
        visited = set()

        def fetch():
            if generation != self.generation or self.service.closed:
                return
            if cursor in visited:
                complete(ValueError("历史分页未前进"))
                return
            visited.add(cursor)
            self.service.api.get("/api/v5/market/history-candles", received,
                                 {"instId": instrument, "bar": bar, "after": str(cursor), "limit": "300"})

        def complete(error):
            if generation != self.generation:
                return
            self.active = False
            rows = self.cache.range(instrument, bar, begin, end)
            gaps = self.cache.gaps(instrument, bar, begin, end)
            self.finished.emit(rows, str(error) if error else (f"历史数据存在 {len(gaps)} 处缺口" if gaps else None))

        def received(rows, error):
            nonlocal cursor
            if generation != self.generation:
                return
            if error:
                complete(error)
                return
            try:
                rows = valid_candles(rows or [])
                if not rows:
                    complete(ValueError("已到数据源历史边界"))
                    return
                self.cache.put(instrument, bar, rows)
                oldest = int(rows[0][0])
                if oldest >= cursor:
                    complete(ValueError("历史分页未前进"))
                    return
                cursor = oldest
                if oldest <= begin or not self.cache.gaps(instrument, bar, begin, end):
                    complete(None)
                else:
                    QTimer.singleShot(350, fetch)
            except (ValueError, TypeError, KeyError) as exc:
                complete(exc)
        fetch()
