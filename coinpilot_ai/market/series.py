"""共享图表数值与 EMA。尾部增量同步完成，大范围修正分片后原子发布。"""
from dataclasses import dataclass, field
from time import perf_counter

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


@dataclass(frozen=True)
class SeriesChange:
    pair: tuple
    revision: int
    first: int | None
    last: int | None
    structural: bool


@dataclass
class SeriesData:
    rows: list = field(default_factory=list)
    times: list = field(default_factory=list)
    values: list = field(default_factory=list)
    emas: list = field(default_factory=list)
    periods: tuple = ()


class ChartSeries(QObject):
    changed = pyqtSignal(object)
    SLICE_SECONDS = .004
    INLINE_ROWS = 128

    def __init__(self, pair, periods, parent=None):
        super().__init__(parent)
        self.pair, self.periods = pair, tuple(periods)
        self.data = SeriesData(emas=[[] for _ in periods], periods=tuple(periods))
        self.revision = 0
        self.converted_rows = 0
        self.slice_samples = []
        self.source = []
        self.job = None
        self.pending = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self._step)

    def cancel(self):
        self.timer.stop()
        self.job = self.pending = None

    def set_periods(self, periods):
        periods = tuple(periods)
        if periods != self.periods:
            self.periods = periods
            self.update(self.source, 0, structural=True)

    def _row(self, target, row, i):
        value = [float(v) for v in row[1:6]]
        stamp, close = int(row[0]), value[3]
        self.converted_rows += 1
        append = i == len(target.rows)
        if append:
            target.rows.append(row)
            target.times.append(stamp)
            target.values.append(value)
        else:
            target.rows[i], target.times[i], target.values[i] = row, stamp, value
        for period, cache in zip(target.periods, target.emas):
            previous = cache[i-1] if i else close
            ema = previous + 2 / (period+1) * (close-previous)
            if append:
                cache.append(ema)
            else:
                cache[i] = ema

    def update(self, rows, start, *, structural=False):
        old_source, self.source = self.source, rows
        if self.job and not structural and rows is old_source:
            self.pending = start if self.pending is None else min(start, self.pending)
            return
        self.cancel()
        start = min(start, len(self.data.rows))
        if not structural and self.data.periods == self.periods and len(rows)-start <= self.INLINE_ROWS:
            for i in range(start, len(rows)):
                self._row(self.data, rows[i], i)
            self._publish(self.data, start, False)
            return
        # 前缀只复制引用；不能在后台任务中修改仍被画布使用的完整结果。
        prefix = 0 if structural or self.data.periods != self.periods else start
        if len(rows) <= 512:
            for _ in self._build(rows, prefix, start, structural):
                pass
            return
        self.job = self._build(rows, prefix, start, structural)
        self.timer.start()

    def _build(self, rows, prefix, first, structural):
        target = SeriesData(emas=[[] for _ in self.periods], periods=self.periods)
        for i in range(prefix):
            target.rows.append(self.data.rows[i])
            target.times.append(self.data.times[i])
            target.values.append(self.data.values[i])
            for cache, old in zip(target.emas, self.data.emas):
                cache.append(old[i])
            yield
        for i in range(prefix, len(rows)):
            self._row(target, rows[i], i)
            yield
        # 计算期间到达的修正必须纳入同一份完整结果；普通实时尾部更新不会重启历史任务。
        while self.pending is not None or len(target.rows) < len(rows):
            start = min(self.pending if self.pending is not None else len(target.rows), len(target.rows))
            self.pending = None
            first = min(first, start)
            for i in range(start, len(rows)):
                self._row(target, rows[i], i)
                yield
        self._publish(target, first, structural)

    def _step(self):
        if self.job is None:
            return
        begin = perf_counter()
        job = self.job
        try:
            while perf_counter()-begin < self.SLICE_SECONDS:
                next(job)
        except StopIteration:
            if self.job is job:
                self.job = None
        self.slice_samples.append((perf_counter()-begin)*1000)
        self.slice_samples = self.slice_samples[-256:]
        if self.job is not None:
            self.timer.start()

    def _publish(self, data, start, structural):
        self.data = data
        # Qt 槽同步执行；观察者可能在通知中送入下一次更新。
        self.job = self.pending = None
        self.revision += 1
        first = data.times[min(start, len(data.times)-1)] if data.times else None
        self.changed.emit(SeriesChange(self.pair, self.revision, first,
                                      data.times[-1] if data.times else None, structural))
