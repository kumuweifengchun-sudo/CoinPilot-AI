"""串行分页同步，覆盖范围与每个接口的结果分开记录。"""
import time

from PyQt6.QtCore import QTimer


class HistorySync:
    def __init__(self, api, store, scope, changed, finished):
        self.api, self.store, self.scope = api, store, scope
        self.changed, self.finished = changed, finished
        self.running = False
        self.cancelled = False

    def start(self, begin, end=None):
        if self.running:
            return False
        end = min(end or time.time(), time.time())
        if begin >= end:
            raise ValueError("历史起始时间必须早于结束时间和当前时间")
        self.running, self.cancelled = True, False
        # 常用归档接口仅覆盖近三个月，较早的本地记录不会被删除。
        effective = max(begin, end - 89 * 86400)
        self.coverage = {"requested_begin": begin, "begin": effective, "end": end, "time": time.time(),
                         "limited": effective > begin, "results": {}, "complete": False}
        common = {"instType": "SWAP", "begin": int(effective * 1000), "end": int(end * 1000), "limit": "100"}
        self.jobs = [
            ("fills", "/api/v5/trade/fills-history", "billId", dict(common)),
            ("orders", "/api/v5/trade/orders-history-archive", "ordId", dict(common)),
            ("bills", "/api/v5/account/bills-archive", "billId", dict(common)),
            ("position_history", "/api/v5/account/positions-history", "uTime", {"instType": "SWAP", "limit": "100"}),
        ]
        self._next()
        return True

    def _next(self):
        if self.cancelled:
            return
        if not self.jobs:
            self.running = False
            self.coverage["complete"] = all(row.get("success") for row in self.coverage["results"].values())
            self.store.append("sync_run", self.coverage, self.scope)
            if self.coverage["complete"]:
                previous = self.store.get("state", "history_cursor", 0, self.scope)
                self.store.put("state", "history_cursor", max(previous, self.coverage["end"]), self.scope)
            self.finished(self.coverage)
            return
        self.kind, self.path, self.id_field, self.params = self.jobs.pop(0)
        self.count, self.pages, self.cursors = 0, 0, set()
        self.earliest, self.latest = None, None
        self._page()

    def _page(self):
        if not self.cancelled:
            self.api.get(self.path, self._received, params=self.params, private=True)

    def _received(self, rows, error):
        if self.cancelled:
            return
        self.pages += 1
        if error:
            self.coverage["results"][self.kind] = {"success": False, "error": str(error), "count": self.count}
            self._next()
            return
        cutoff = False
        for row in rows:
            stamp = int(row.get("fillTime") or row.get("ts") or row.get("uTime") or row.get("cTime") or 0) / 1000
            if self.kind == "position_history" and stamp < self.coverage["begin"]:
                cutoff = True
                continue
            if not row.get("instId", "").endswith("-USDT-SWAP"):
                continue
            key = row.get(self.id_field)
            if self.kind == "position_history":
                key = str(row.get("posId")) + ":" + str(row.get("uTime"))
            if key is None:
                continue
            self.store.put(self.kind, str(key), row, self.scope)
            self.count += 1
            self.earliest = min(self.earliest or stamp, stamp)
            self.latest = max(self.latest or stamp, stamp)
            self.changed(self.kind, row)
        cursor = str(rows[-1].get(self.id_field, "")) if rows else ""
        if len(rows) >= 100 and not cutoff:
            if not cursor or cursor in self.cursors or self.pages >= 1000:
                self.coverage["results"][self.kind] = {"success": False, "count": self.count,
                                                     "error": "分页未能继续，覆盖范围不完整"}
                self._next()
                return
            self.cursors.add(cursor)
            self.params["after"] = cursor
            QTimer.singleShot(500, self._page)
            return
        self.coverage["results"][self.kind] = {"success": True, "count": self.count,
                                             "earliest": self.earliest, "latest": self.latest}
        QTimer.singleShot(500, self._next)

    def cancel(self):
        self.cancelled, self.running = True, False
