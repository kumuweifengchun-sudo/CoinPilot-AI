"""共享数值快照；尾部原位增量，大范围转换在后台原子发布。"""
from array import array
from dataclasses import dataclass, field
from collections import deque
from time import perf_counter
from PyQt6.QtCore import QObject, pyqtSignal
from .buffer import CandleBuffer, ValueView
from .intervals import BARS, contiguous
from .jobs import WorkQueue

@dataclass(frozen=True)
class SeriesChange:
    pair: tuple
    revision: int
    first: int | None
    last: int | None
    structural: bool

@dataclass
class SeriesData:
    rows: CandleBuffer = field(default_factory=CandleBuffer)
    emas: list = field(default_factory=list)
    periods: tuple = ()

    @property
    def times(self):
        return self.rows.times

    @property
    def values(self):
        return ValueView(self.rows)

class ChartSeries(QObject):
    changed = pyqtSignal(object)
    INLINE_ROWS = 128

    def __init__(self, pair, periods, parent=None):
        super().__init__(parent)
        self.pair, self.periods = pair, tuple(periods)
        self.pair_interval = BARS[pair[1]]*1000
        self.data = SeriesData(emas=[array("d") for _ in periods], periods=tuple(periods))
        self.revision = 0
        self.updates = deque(maxlen=128)
        self.last_dropped = self.last_start = 0
        self.last_structural = True
        self.converted_rows = 0
        self.worker_samples = []
        self.source = CandleBuffer()
        self.job = self.pending = None
        self.generation = 0
        self.worker = getattr(getattr(parent, "service", None), "compute", None)
        if self.worker is None:
            self.worker = WorkQueue(self)
            self.destroyed.connect(lambda _, worker=self.worker: worker.close())

    def cancel(self):
        self.generation += 1
        if self.job is not None:
            self.worker.cancel(self.job)
        self.job = self.pending = None

    def set_periods(self, periods):
        periods = tuple(periods)
        if periods != self.periods:
            self.periods = periods
            self.update(self.source, 0, structural=True)

    @staticmethod
    def convert(target, row, index, interval, bar=None):
        append = index == len(target.rows)
        if append:
            target.rows.append(row)
        else:
            target.rows[index] = row
        adjacent = index > 0 and (contiguous(target.times[index-1], row[0], bar) if bar else row[0]-target.times[index-1] == interval)
        for period, values in zip(target.periods, target.emas):
            previous = values[index-1] if adjacent else row[4]
            value = previous+2/(period+1)*(row[4]-previous)
            if append:
                values.append(value)
            else:
                values[index] = value

    def update(self, rows, start, *, structural=False, dropped=0):
        rows = rows if isinstance(rows, CandleBuffer) else CandleBuffer(rows)
        old_source, self.source = self.source, rows
        if self.job is not None and not structural and not dropped and rows is old_source:
            self.pending = start if self.pending is None else min(start, self.pending)
            return
        busy = self.job is not None
        self.cancel()
        if dropped:
            if busy or dropped >= len(self.data.rows) or self.data.periods != self.periods:
                structural, start, dropped = True, 0, 0
            else:
                del self.data.rows[:dropped]
                for values in self.data.emas:
                    del values[:dropped]
        start = min(start, len(self.data.rows))
        if not structural and self.data.periods == self.periods and len(rows)-start <= self.INLINE_ROWS:
            for i in range(start, len(rows)):
                self.convert(self.data, rows[i], i, self.pair_interval, self.pair[1])
            self.converted_rows += len(rows)-start
            self._publish(self.data, start, False, dropped=dropped)
            return
        prefix = 0 if structural or dropped or self.data.periods != self.periods else start
        snapshot = CandleBuffer(rows)
        periods, interval, generation = self.periods, self.pair_interval, self.generation
        old_emas = [values[:prefix] for values in self.data.emas] if prefix else [array("d") for _ in periods]
        def build():
            begin = perf_counter()
            target = SeriesData(snapshot[:prefix], old_emas, periods)
            for i in range(prefix, len(snapshot)):
                self.convert(target, snapshot[i], i, interval, self.pair[1])
            return target, (perf_counter()-begin)*1000
        def done(value, error):
            if generation != self.generation:
                return
            self.job = None
            if error:
                return
            target, elapsed = value
            self.worker_samples.append(elapsed)
            self.worker_samples = self.worker_samples[-64:]
            self.converted_rows += len(snapshot)-prefix
            pending = self.pending if self.pending is not None else len(target.rows)
            self.pending = None
            if len(self.source)-pending > self.INLINE_ROWS:
                self.update(self.source, 0, structural=True)
                return
            for i in range(pending, len(self.source)):
                self.convert(target, self.source[i], i, interval, self.pair[1])
            self.converted_rows += len(self.source)-pending
            self._publish(target, min(start, pending), structural or bool(dropped))
        self.job = self.worker.submit(build, done)

    def _publish(self, data, start, structural, *, dropped=0):
        self.data = data
        self.job = self.pending = None
        self.revision += 1
        self.last_start, self.last_structural, self.last_dropped = start, structural, dropped
        self.updates.append((self.revision, start, structural, dropped))
        first = data.times[min(start, len(data.times)-1)] if data.times else None
        self.changed.emit(SeriesChange(self.pair, self.revision, first,
                                      data.times[-1] if data.times else None, structural))
