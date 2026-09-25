"""公开 Funding、OI、Mark、Index 与 Basis 快照。"""
import time
from collections import deque
from decimal import Decimal

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from coinpilot_ai.trading.models import number


class DerivativeMonitor(QObject):
    changed = pyqtSignal(str)

    def __init__(self, service):
        super().__init__(service)
        self.service = service
        self.values = {}
        self.history = {}
        self.oi_history = {}
        self.queue = deque()
        self.generation = 0
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.step)

    def schedule(self, instruments):
        self.queue = deque((inst, kind) for inst in sorted(instruments)
                           for kind in ("funding", "oi", "mark", "index"))
        if self.queue:
            self.timer.start()

    def step(self):
        if self.service.closed or not self.queue:
            self.timer.stop()
            return
        inst, kind = self.queue.popleft()
        paths = {
            "funding": ("/api/v5/public/funding-rate", {"instId": inst}),
            "oi": ("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}),
            "mark": ("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst}),
            "index": ("/api/v5/market/index-tickers", {"instId": inst.replace("-SWAP", "")})}
        path, params = paths[kind]
        generation = self.generation
        self.service.api.get(path, lambda rows, error: self.received(inst, kind, rows, error, generation), params)

    def received(self, inst, kind, rows, error, generation):
        if generation != self.generation or self.service.closed or error or not rows:
            return
        row = rows[0]
        try:
            field = {"funding": "fundingRate", "oi": "oiCcy", "mark": "markPx", "index": "idxPx"}[kind]
            raw = row.get(field) or (row.get("oi") if kind == "oi" else None)
            value = number(raw)
            stamp = int(row.get("ts") or time.time()*1000)/1000
            if not -5 <= time.time()-stamp <= 120:
                return
            record = self.values.setdefault(inst, {})
            record[kind] = {"value": value, "time": stamp, "source": "okx"}
            self.remember(inst, kind, stamp, value)
            if kind == "oi":
                history = self.oi_history.setdefault(inst, deque())
                history.append((stamp, value))
                while history and stamp-history[0][0] > 86400:
                    history.popleft()
            if "mark" in record and "index" in record and record["index"]["value"] > 0:
                record["basis"] = {"value": (record["mark"]["value"]/record["index"]["value"]-1)*100,
                    "time": min(record["mark"]["time"], record["index"]["time"]), "source": "okx"}
                self.remember(inst, "basis", record["basis"]["time"], record["basis"]["value"])
            self.changed.emit(inst)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return

    def remember(self, inst, kind, stamp, value):
        history = self.history.setdefault((inst, kind), deque())
        if history and stamp <= history[-1][0]:
            if stamp == history[-1][0]:
                history[-1] = (stamp, value)
            return
        history.append((stamp, value))
        while history and stamp-history[0][0] > 86400:
            history.popleft()

    def metric(self, inst, kind, now, window=900):
        if kind == "oi_change_pct":
            current = self.values.get(inst, {}).get("oi")
            if not current or now-current["time"] > 120:
                return None
            target = current["time"]-window
            base = next((value for stamp, value in reversed(self.oi_history.get(inst, ()))
                         if stamp <= target and target-stamp <= 90), None)
            return (current["value"]/base-1)*100 if base else None
        record = self.values.get(inst, {}).get(kind)
        return record["value"] if record and now-record["time"] <= 120 else None

    def reset(self):
        self.generation += 1
        self.queue.clear()
        self.timer.stop()
        self.values.clear()
        self.history.clear()
        self.oi_history.clear()
