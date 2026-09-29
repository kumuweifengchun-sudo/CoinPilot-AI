"""可回退最后一次推入的滚动统计，末根报价修正无需复制窗口。"""
from collections import deque


class RollingMoments:
    def __init__(self, period):
        self.period = period
        self.values = deque()
        self.count, self.mean, self.m2 = 0, 0.0, 0.0
        self.previous = None

    def push(self, value):
        state = self.count, self.mean, self.m2
        evicted = self.values.popleft() if len(self.values) == self.period else ...
        if evicted is not ... and evicted is not None:
            if self.count == 1:
                self.count, self.mean, self.m2 = 0, 0.0, 0.0
            else:
                mean = (self.mean*self.count-evicted)/(self.count-1)
                self.m2 -= (evicted-self.mean)*(evicted-mean)
                self.count -= 1
                self.mean = mean
        self.values.append(value)
        if value is not None:
            self.count += 1
            delta = value-self.mean
            self.mean += delta/self.count
            self.m2 += delta*(value-self.mean)
        self.previous = state, evicted
        return self.mean if self.count == self.period else None

    def undo(self):
        if self.previous is not None:
            state, evicted = self.previous
            self.values.pop()
            if evicted is not ...:
                self.values.appendleft(evicted)
            self.count, self.mean, self.m2 = state
            self.previous = None


class RollingExtreme:
    def __init__(self, period, *, maximum):
        self.period, self.maximum = period, maximum
        self.values = deque()
        self.index = 0
        self.previous = None

    def push(self, value):
        removed = []
        expired = self.values.popleft() if self.values and self.values[0][0] <= self.index-self.period else None
        while self.values and ((value >= self.values[-1][1]) if self.maximum else (value <= self.values[-1][1])):
            removed.append(self.values.pop())
        self.values.append((self.index, value))
        self.index += 1
        self.previous = expired, removed
        return self.values[0][1] if self.index >= self.period else None

    def undo(self):
        if self.previous is not None:
            expired, removed = self.previous
            self.values.pop()
            self.values.extend(reversed(removed))
            if expired is not None:
                self.values.appendleft(expired)
            self.index -= 1
            self.previous = None
