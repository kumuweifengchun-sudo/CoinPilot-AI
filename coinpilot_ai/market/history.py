"""磁盘快照：固定宽度数值记录、有限页缓存，不把整个回测历史放进 RAM。"""
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from struct import Struct
from .buffer import CandleBuffer, candle
from .intervals import BARS, contiguous, floor_time, shift
from bisect import bisect_left, bisect_right


class HistoryData(Sequence):
    RECORD = Struct("<q7dB")
    PAGE = 1024

    def __init__(self, path, count):
        self.path, self.count = Path(path), count
        self.file = self.path.open("rb")
        self.pages = OrderedDict()
        self.lock = RLock()
        self.closed = False

    @classmethod
    def create(cls, rows, interval, *, bar=None, cancelled=lambda: False):
        file = NamedTemporaryFile(prefix="coinpilot-history-", suffix=".bin", delete=False)
        count, previous = 0, None
        try:
            for row in rows:
                if count % cls.PAGE == 0 and cancelled():
                    raise ValueError("历史读取已取消")
                row = candle(row)
                if not row[8] or (previous is not None and
                        (not contiguous(previous, row[0], bar) if bar else row[0]-previous != interval)):
                    raise ValueError("历史 K 线未收盘或存在缺口")
                file.write(cls.RECORD.pack(*row))
                count += 1
                previous = row[0]
            file.close()
            return cls(file.name, count)
        except Exception:
            file.close()
            Path(file.name).unlink(missing_ok=True)
            raise

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return CandleBuffer(self[i] for i in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        page, offset = divmod(index, self.PAGE)
        with self.lock:
            if page not in self.pages:
                self.file.seek(page*self.PAGE*self.RECORD.size)
                self.pages[page] = self.file.read(self.PAGE*self.RECORD.size)
            self.pages.move_to_end(page)
            while len(self.pages) > 2:
                self.pages.popitem(last=False)
            return self.RECORD.unpack_from(self.pages[page], offset*self.RECORD.size)

    def window(self, cursor, left, count, base, bar, *, cancelled=lambda: False):
        """仅聚合游标以前的已知记录，按显示周期读视口和 300 根预热。"""
        step, interval = BARS[base]*1000, BARS[bar]*1000
        begin = shift(floor_time(max(0, left), bar), bar, -300)
        end = min(self[cursor][0], left+count*step+interval)
        start = bisect_left(self, begin, hi=cursor+1, key=lambda row: row[0])
        stop = bisect_right(self, end, hi=cursor+1, key=lambda row: row[0])
        if base == bar:
            return self[start:stop]
        result, group, samples, bucket = CandleBuffer(), None, 0, None
        def flush():
            if group is not None:
                group[8] = int(group_start == bucket and shift(last, base) == shift(bucket, bar))
                result.append(tuple(group))
        for index in range(start, stop):
            if index % 1024 == 0 and cancelled():
                raise ValueError("回放视口读取已取消")
            row = self[index]
            stamp = floor_time(row[0], bar)
            if stamp != bucket:
                flush()
                group, bucket, samples, group_start = list(row), stamp, 1, row[0]
                group[0] = stamp
            else:
                samples += 1
                group[2], group[3], group[4] = max(group[2], row[2]), min(group[3], row[3]), row[4]
                for column in (5, 6, 7):
                    group[column] += row[column]
            last = row[0]
        flush()
        return result

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.file.close()
                self.pages.clear()
                self.path.unlink(missing_ok=True)

    def __del__(self):
        if hasattr(self, "lock"):
            self.close()
