"""公开 Funding、OI、Mark、Index 与 Basis 快照。"""
import time
from collections import deque
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from coinpilot_ai.trading.models import number
from coinpilot_ai.integrations.transport import ApiError

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


class DerivativeValue(TypedDict):
    value: Decimal
    time: float
    source: str


class DerivativeMonitor(QObject):
    changed = pyqtSignal(str)

    def __init__(self, service: "CockpitService") -> None:
        super().__init__(service)
        self.service = service
        self.values: dict[str, dict[str, DerivativeValue]] = {}
        self.history: dict[tuple[str, str], deque[tuple[float, Decimal]]] = {}
        self.oi_history: dict[str, deque[tuple[float, Decimal]]] = {}
        self.queue: deque[tuple[str, str]] = deque()
        self.pending: set[tuple[str, str]] = set()
        self.errors: dict[tuple[str, str], str] = {}
        self.generation = 0
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.step)

    def schedule(self, instruments: Iterable[str]) -> None:
        if self.service.closed:
            return
        queued = set(self.queue) | self.pending
        for inst in sorted(set(instruments)):
            for kind in ("funding", "oi", "mark", "index"):
                if (inst, kind) not in queued:
                    self.queue.append((inst, kind))
        if self.queue:
            self.timer.start()

    def step(self) -> None:
        if self.service.closed or not self.queue:
            self.timer.stop()
            return
        inst, kind = self.queue.popleft()
        self.pending.add((inst, kind))
        paths = {
            "funding": ("/api/v5/public/funding-rate", {"instId": inst}),
            "oi": ("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}),
            "mark": ("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst}),
            "index": ("/api/v5/market/index-tickers", {"instId": inst.replace("-SWAP", "")})}
        path, params = paths[kind]
        generation = self.generation
        self.service.api.get(path, lambda rows, error: self.received(inst, kind, rows, error, generation), params)

    def received(self, inst: str, kind: str, rows: object, error: ApiError | None, generation: int) -> None:
        if generation != self.generation or self.service.closed:
            return
        self.pending.discard((inst, kind))
        if error or not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            self.errors[(inst, kind)] = "公开数据请求失败" if error else "暂无公开数据"
            self.changed.emit(inst)
            return
        row = rows[0]
        try:
            field = {"funding": "fundingRate", "oi": "oiCcy", "mark": "markPx", "index": "idxPx"}[kind]
            raw = row.get(field)
            if raw in (None, "") and kind == "oi":
                raw = row.get("oi")
            value = number(raw)
            stamp = int(row.get("ts") or time.time()*1000)/1000
            if not -5 <= time.time()-stamp <= 120:
                raise ValueError("快照已过期")
            self.errors.pop((inst, kind), None)
            record = self.values.setdefault(inst, {})
            record[kind] = {"value": value, "time": stamp, "source": "okx"}
            self.remember(inst, kind, stamp, value)
            if kind == "oi":
                history = self.oi_history.setdefault(inst, deque())
                history.append((stamp, value))
                while history and stamp-history[0][0] > 86400:
                    history.popleft()
            if (kind in ("mark", "index") and "mark" in record and "index" in record
                    and record["index"]["value"] > 0
                    and all(-5 <= time.time()-record[key]["time"] <= 120 for key in ("mark", "index"))):
                record["basis"] = {"value": (record["mark"]["value"]/record["index"]["value"]-1)*100,
                    "time": min(record["mark"]["time"], record["index"]["time"]), "source": "okx"}
                self.remember(inst, "basis", record["basis"]["time"], record["basis"]["value"])
            self.changed.emit(inst)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            self.errors[(inst, kind)] = "公开数据无效或已过期"
            self.changed.emit(inst)

    def remember(self, inst: str, kind: str, stamp: float, value: Decimal) -> None:
        history = self.history.setdefault((inst, kind), deque())
        if history and stamp <= history[-1][0]:
            if stamp == history[-1][0]:
                history[-1] = (stamp, value)
            return
        history.append((stamp, value))
        while history and stamp-history[0][0] > 86400:
            history.popleft()

    def metric(self, inst: str, kind: str, now: float, window: float = 900) -> Decimal | None:
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

    def reset(self) -> None:
        self.generation += 1
        self.queue.clear()
        self.pending.clear()
        self.errors.clear()
        self.timer.stop()
        self.values.clear()
        self.history.clear()
        self.oi_history.clear()
