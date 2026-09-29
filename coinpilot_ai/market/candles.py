"""OKX 公共 K 线：历史分页、REST 校准和业务 WebSocket。

与工作台现有公共报价使用相同的生产行情源；模拟/真实仅隔离账户及图表状态。
"""
import json
import math
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebSockets import QWebSocket

from coinpilot_ai.market.intervals import BARS, shift, floor_time, openings
from .series import ChartSeries

from .buffer import CandleBuffer, CandleIndex, candle, candles as valid_candles


class CandleStream(QObject):
    def __init__(self, proxy, receive, status, parent=None, socket_factory=None):
        super().__init__(parent)
        self.proxy, self.receive, self.status = proxy, receive, status
        self.socket_factory = socket_factory or (lambda: QWebSocket(parent=self))
        self.socket = None
        self.pair = None
        self.closed = False
        self.failures = 0
        self.last_message = self.last_ping = 0
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.start)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(1000)
        self.watchdog.timeout.connect(self.check)

    def select(self, pair):
        if self.pair == pair:
            return
        self.drop()
        self.retry.stop()
        self.pair = pair
        self.failures = 0
        self.start()

    def start(self):
        if self.closed or self.socket is not None or not self.pair:
            return
        socket = self.socket_factory()
        self.socket = socket
        pair = self.pair
        socket.setProxy(self.proxy)
        socket.connected.connect(lambda: self.connected(socket, pair))
        socket.textMessageReceived.connect(lambda msg: self.message(socket, pair, msg))
        socket.disconnected.connect(lambda: self.failed(socket))
        socket.errorOccurred.connect(lambda _: self.failed(socket))
        self.last_message = self.last_ping = time.monotonic()
        self.status("重连中")
        self.watchdog.start()
        socket.open(QUrl("wss://ws.okx.com:8443/ws/v5/business"))

    def connected(self, socket, pair):
        if socket is self.socket:
            socket.sendTextMessage(json.dumps({"op": "subscribe", "args": [{"channel": "candle" + pair[1], "instId": pair[0]}]}))

    def message(self, socket, pair, message):
        if self.closed or socket is not self.socket or pair != self.pair:
            return
        if message == "pong":
            return  # 心跳不能使过期行情变为新鲜。
        try:
            payload = json.loads(message)
            if payload.get("event") == "error":
                self.failed(socket)
                return
            arg = payload.get("arg", {})
            if arg.get("instId") != pair[0] or arg.get("channel") != "candle" + pair[1] or "data" not in payload:
                return
            rows = valid_candles(payload["data"])
            if not rows:
                return
        except (ValueError, TypeError, KeyError, OverflowError):
            self.failed(socket)
            return
        self.last_message = time.monotonic()
        self.failures = 0
        self.receive(pair, rows)
        self.status("实时")

    def check(self):
        now = time.monotonic()
        if now - self.last_message >= 30:
            self.failed(self.socket)
        elif self.socket is not None and now - self.last_ping >= 15:
            self.last_ping = now
            self.socket.sendTextMessage("ping")

    def failed(self, socket):
        if socket is not self.socket or self.closed or self.retry.isActive():
            return
        self.drop()
        self.failures += 1
        self.status("轮询 · WebSocket 重连中")
        self.retry.start(min(30000, 1000 * 2 ** min(self.failures - 1, 5)))

    def drop(self):
        old, self.socket = self.socket, None
        self.watchdog.stop()
        if old is not None:
            old.abort()
            old.deleteLater()

    def close(self):
        self.closed = True
        self.retry.stop()
        self.drop()


class ChartFeed(QObject):
    series_changed = pyqtSignal(object)
    indicators_changed = pyqtSignal(object)
    INACTIVE_PAIR_LIMIT = 8
    MEMORY_CANDLE_LIMIT = 16000
    LIVE_TAIL_LIMIT = 6000
    STARTUP_CACHE_LIMIT = 6000
    INDICATOR_CACHE_LIMIT = 32

    def __init__(self, service):
        super().__init__(service)
        self.service = service
        self.generation = 0
        self.pending = set()
        self.history_state = {}
        self.revisions = {}
        self.stream_versions = {}
        self.stream_state = "等待行情"
        self.stream = None
        self.extra_streams = {}
        self.pair_status = {}
        self.repairs = {}
        self.window_attempts = {}
        self.view_windows = {}
        self.display_refresh = {}
        self.derived_stamps = set()
        self.series = {}
        self.indicator_cache = {}
        self.indicator_pending = {}
        self.raw_indexes = {}
        self.recent_pairs = OrderedDict()
        service.chart_book.changed.connect(self.settings_changed)

    def settings_changed(self, kind, _key):
        if kind == "ema":
            periods = [e["period"] for e in self.service.chart_book.emas]
            for series in self.series.values():
                series.set_periods(periods)

    def raw_index(self, pair):
        self.recent_pairs[pair] = None
        self.recent_pairs.move_to_end(pair)
        rows = self.service.candles.get(pair)
        if not isinstance(rows, CandleBuffer):
            rows = self.service.candles[pair] = CandleBuffer(rows or ())
        cached = self.raw_indexes.get(pair)
        if cached is None or cached[0] is not rows:
            cached = (rows, rows.times, CandleIndex(rows))
            self.raw_indexes[pair] = cached
        return cached

    def prune_inactive(self):
        """保留监控和请求中的数据，仅淘汰最近未使用的行情缓存，不删除业务记录。"""
        if len(self.recent_pairs) <= self.INACTIVE_PAIR_LIMIT:
            return
        s = self.service
        protected = {(s.selected, s.bar)} | s.chart_pairs | s.indicator_pairs
        protected.update(key[0] for key in self.pending)
        protected.update(self.repairs)
        protected.update(self.view_windows)
        for _, rule in s.rules():
            if rule.get("enabled", True):
                protected.update((rule["instrument"], condition.get("bar", "15m"))
                                 for condition in rule.get("conditions", [])
                                 if condition["metric"] in ("volume_ratio", "indicator"))
                if rule.get("trigger") == "bar":
                    protected.add((rule["instrument"], rule.get("trigger_bar", "1m")))
        inactive = [pair for pair in self.recent_pairs if pair not in protected]
        for pair in inactive[:-self.INACTIVE_PAIR_LIMIT]:
            self.recent_pairs.pop(pair, None)
            self.derived_stamps = {key for key in self.derived_stamps if key[0] != pair}
            series = self.series.pop(pair, None)
            if series is not None:
                series.cancel()
                series.deleteLater()
            for pending, token in list(self.indicator_pending.items()):
                if pending[0] == pair:
                    self.service.compute.cancel(token)
                    self.indicator_pending.pop(pending)
            for cache in (s.candles, s.candle_times, self.raw_indexes, self.indicator_cache,
                          self.revisions, self.history_state, self.pair_status, self.view_windows):
                cache.pop(pair, None)
            for cache in (self.stream_versions, self.window_attempts):
                for key in list(cache):
                    if key[0] == pair:
                        cache.pop(key)

    def get_series(self, pair):
        series = self.series.get(pair)
        if series is None:
            series = ChartSeries(pair, [e["period"] for e in self.service.chart_book.emas], self)
            series.changed.connect(self.series_changed)
            self.series[pair] = series
        rows = self.raw_index(pair)[0]
        if series.source is not rows:
            series.update(rows, 0, structural=True)
        return series

    def rule_indicator(self, pair, rows, spec):
        return self.indicator(self.get_series(pair), spec)

    @staticmethod
    def indicator_delta(series, revision):
        changes = [change for change in series.updates if change[0] > revision]
        if not changes or changes[0][0] != revision+1 or any(change[2] for change in changes):
            return None
        start, dropped = changes[0][1], changes[0][3]
        for _, first, _, removed in changes[1:]:
            start = min(max(0, start-removed), first)
            dropped += removed
        return start, dropped

    def indicator(self, series, spec):
        from coinpilot_ai.market.indicators import IndicatorState, IndicatorResult, validate_spec
        normalized = validate_spec(spec)
        pair = series.pair
        values = self.indicator_cache.setdefault(pair, OrderedDict())
        key = (normalized["kind"], tuple(sorted(normalized["params"].items())))
        cached = values.get(key)
        if cached is not None and cached[0] == series.revision:
            values.move_to_end(key)
            return cached[1].result
        delta = self.indicator_delta(series, cached[0]) if cached else None
        if delta is not None:
            start, dropped = delta
            state = cached[1]
            if start >= max(0, len(state.times)-dropped-1) and len(series.data.rows)-start <= 128:
                state.update(series.data.rows, start, dropped=dropped)
                values[key] = (series.revision, state)
                return state.result
        pending = pair, key
        if pending not in self.indicator_pending:
            snapshot, revision, generation = CandleBuffer(series.data.rows), series.revision, self.generation
            def build():
                state = IndicatorState(normalized, bar=pair[1])
                state.update(snapshot, 0)
                return state
            def ready(state, error):
                self.indicator_pending.pop(pending, None)
                if generation != self.generation or self.service.closed or error:
                    return
                if series.revision != revision:
                    delta = self.indicator_delta(series, revision)
                    if (delta is None or len(series.data.rows)-delta[0] > 128 or
                            delta[0] < max(0, len(state.times)-delta[1]-1)):
                        self.indicator(series, normalized)
                        return
                    state.update(series.data.rows, delta[0], dropped=delta[1])
                values[key] = (series.revision, state)
                values.move_to_end(key)
                while len(values) > self.INDICATOR_CACHE_LIMIT:
                    values.popitem(last=False)
                self.indicators_changed.emit(pair)
                self.service.updated.emit("indicators")
            self.indicator_pending[pending] = self.service.compute.submit(build, ready)
        return IndicatorResult(normalized["kind"], (), {}, ())

    def set_view(self, owner, pair=None, left=None, count=0):
        """一个图窗只拥有自己的登记，跟随实时/隐藏/切换时不影响其他图窗。"""
        for key in list(self.view_windows):
            self.view_windows[key].pop(owner, None)
            if not self.view_windows[key]:
                self.view_windows.pop(key)
        if pair is not None and left is not None:
            self.view_windows.setdefault(pair, {})[owner] = (left, min(10000, max(1, math.ceil(count))))

    def _cache_rows(self, pair, rows):
        if rows:
            snapshot = CandleBuffer(rows)
            generation = self.generation
            def done(_, error):
                if generation == self.generation and error and not self.service.closed:
                    self.history_state[pair] = "本地缓存写入失败"
                    self.service.updated.emit("chart_status")
            self.service.io.submit(lambda: self.service.market_cache.put(*pair, snapshot), done)

    def _trim_memory(self, pair, ordered, times, old):
        """先保留所有可见区间与实时尾部，再公平分配预热/预取预算。

        返回删掉的前缀长度；非连续裁剪返回 None。预留一批增长空间，
        避免每新增一根都移动整份列表。六图各 2000 根时仍为有限窗口。
        """
        windows = list(self.view_windows.get(pair, {}).values())
        limit = max(self.MEMORY_CANDLE_LIMIT,
                    self.LIVE_TAIL_LIMIT + sum(count+1 for _, count in windows))
        if len(ordered) <= limit:
            return 0
        reserve = min(1000, max(1, limit//10))
        target = limit-reserve
        size = len(ordered)
        live_start = max(0, size-self.LIVE_TAIL_LIMIT)
        if not windows:
            indexes = list(range(max(0, size-target), size))
        else:
            selected = set(range(live_start, size))
            bounds = []
            interval = BARS[pair[1]]*1000
            for left, count in windows:
                begin = bisect_left(times, int(left))
                end = bisect_right(times, int(left+count*interval))
                selected.update(range(begin, end))
                bounds.append((begin, end))
            target = max(target, len(selected))
            # 必需数据永不被预取挤走；前置预热与后置预取轮流分配。
            for distance in range(1, 5001):
                if len(selected) >= target:
                    break
                for begin, end in bounds:
                    for index in (begin-distance, end+distance-1):
                        if 0 <= index < size and len(selected) < target:
                            selected.add(index)
            indexes = sorted(selected)
        removed = indexes[0] if indexes and indexes == list(range(indexes[0], size)) else None
        if removed is not None:
            del ordered[:removed]
        else:
            from array import array
            for column in ordered.columns:
                column[:] = array(column.typecode, (column[i] for i in indexes))
            ordered.revision += 1
        self.derived_stamps = {key for key in self.derived_stamps if key[0] != pair or key[1] in old}
        for key in [key for key in self.stream_versions if key[0] == pair and key[1] not in old]:
            self.stream_versions.pop(key, None)
        return removed

    def activate(self):
        if self.service.closed:
            return
        if self.stream is None:
            self.stream = CandleStream(self.service.market.proxy, self.receive, self.status, self)
        self.stream.select((self.service.selected, self.service.bar))
        wanted = (set(getattr(self.service, "chart_pairs", set())) |
                  set(getattr(self.service, "indicator_pairs", set())) | set(self.view_windows)) - {(self.service.selected, self.service.bar)}
        for pair in list(self.extra_streams):
            if pair not in wanted:
                old = self.extra_streams.pop(pair)
                old.close()
                old.deleteLater()
                self.pair_status.pop(pair, None)
        for pair in wanted:
            if pair not in self.extra_streams:
                stream = CandleStream(self.service.market.proxy, self.receive,
                                      lambda value, p=pair: self.status_for(p, value), self)
                self.extra_streams[pair] = stream
                stream.select(pair)

    def status_for(self, pair, value):
        self.pair_status[pair] = value
        if not self.service.closed:
            self.service.updated.emit("chart_status")

    def status(self, value):
        self.stream_state = value
        if not self.service.closed:
            self.service.updated.emit("chart_status")

    def receive(self, pair, rows):
        now = time.monotonic()
        for row in rows:
            self.stream_versions[(pair, int(row[0]))] = now
        self.merge(pair, rows, fresh=True, confirmed_only=True)

    def merge(self, pair, rows, *, fresh=False, started=None, persist=True, confirmed_only=False):
        s = self.service
        if pair not in s.candles:
            s.candles[pair] = CandleBuffer()
        ordered, times, old = self.raw_index(pair)
        original_count, first = len(ordered), len(ordered)
        additions = {}
        changed = False
        accepted = []
        for row in rows:
            row = candle(row)
            stamp = row[0]
            previous = additions.get(stamp, old.get(stamp))
            if previous and str(previous[8]) == "1" and str(row[8]) != "1":
                continue
            if started is not None and self.stream_versions.get((pair, stamp), 0) > started:
                if str(row[8]) != "1" or (previous and str(previous[8]) == "1"):
                    continue
            if persist:
                self.derived_stamps.discard((pair, stamp))
            if not confirmed_only or str(row[8]) == "1":
                accepted.append(row)
            if previous == row:
                continue
            # 发布的行是不可变数值元组。
            if previous is None or stamp in additions:
                additions[stamp] = row
            else:
                index = bisect_left(times, stamp)
                ordered[index] = row
                first = min(first, index)
            changed = True
        if persist:
            self._cache_rows(pair, accepted)
        # 按插入位置成组拷贝，追加/前补各只移动一次列表；不对全部历史排序。
        groups = {}
        for stamp in sorted(additions):
            index = bisect_left(times, stamp)
            groups.setdefault(index, []).append(stamp)
            first = min(first, index)
        structural = any(index < original_count for index in groups)
        for index, stamps in reversed(list(groups.items())):
            ordered[index:index] = [additions[t] for t in stamps]
        # 只有新增时间戳会增长内存，普通末根修正不扫描或裁剪历史。
        dropped = self._trim_memory(pair, ordered, times, old) if additions else 0
        structural = structural or dropped is None
        if structural:
            first, dropped = 0, 0
        else:
            first = max(0, first-dropped)
        if changed:
            self.revisions[pair] = self.revisions.get(pair, 0) + 1
            series = self.series.get(pair)
            if series is not None:
                series.update(ordered, first, structural=structural, dropped=dropped)
        if fresh and ordered and rows and int(rows[-1][0]) >= int(ordered[-1][0]) and pair not in self.repairs:
            latest = int(ordered[-1][0]) / 1000
            if latest-5 <= time.time() <= shift(int(ordered[-1][0]), pair[1])/1000+30:
                s.candle_times[pair] = time.time()
            else:
                s.candle_times.pop(pair, None)
        self.prune_inactive()
        s.updated.emit("candles")

    def fetch(self, inst, bar, *, older=False, _cached=False, _startup=False):
        s, pair = self.service, (inst, bar)
        key = (pair, older)
        if s.closed or key in self.pending:
            return
        current = s.candles.get(pair, [])
        if older and (not current or self.history_state.get(pair) == "已到历史边界"):
            return
        if not _cached and (older or not current):
            self.pending.add(key)
            generation = self.generation
            boundary = int(current[0][0]) if current else None
            step = BARS[bar]*1000
            def read():
                if older:
                    return s.market_cache.range(inst, bar, shift(boundary, bar, -300), shift(boundary, bar, -1))
                return s.market_cache.tail(inst, bar, self.STARTUP_CACHE_LIMIT)
            def loaded(rows, error):
                if generation != self.generation or s.closed:
                    return
                self.pending.discard(key)
                if not error and rows and (not older or len(rows) == 300):
                    if older or not s.candles.get(pair):
                        self.merge(pair, rows, persist=False)
                    if older:
                        self.history_state[pair] = ""
                        return
                self.fetch(inst, bar, older=older, _cached=True, _startup=not older)
            s.io.submit(read, loaded)
            return
        self.pending.add(key)
        generation, started = self.generation, time.monotonic()
        previous_last = int(current[-1][0]) if current and not _startup else None
        params = {"instId": inst, "bar": bar, "limit": "300"}
        boundary = int(current[0][0]) if older else None
        if older:
            params["after"] = str(boundary)
            self.history_state[pair] = "加载更早 K 线…"
            s.updated.emit("chart_status")

        def done(rows, error):
            if generation != self.generation or s.closed:
                return
            self.pending.discard(key)
            try:
                if error:
                    raise ValueError(str(error))
                rows = valid_candles(rows or [])
            except (ValueError, TypeError, OverflowError):
                if older:
                    self.history_state[pair] = "历史加载失败 · 点击重试"
                elif time.time() - s.candle_times.get(pair, 0) > 30:
                    s.candle_times.pop(pair, None)
                s.updated.emit("chart_status")
                return
            if older:
                rows = [row for row in rows if int(row[0]) < boundary]
                self.history_state[pair] = "" if rows else "已到历史边界"
            elif rows and previous_last and int(rows[0][0]) > shift(previous_last, bar):
                self.repairs[pair] = {"cursor": int(rows[0][0]), "target": previous_last}
                s.candle_times.pop(pair, None)
            self.merge(pair, rows, fresh=not older, started=started)
            if pair in self.repairs:
                QTimer.singleShot(350, lambda: self.repair(pair) if generation == self.generation else None)
        path = "/api/v5/market/history-candles" if older else "/api/v5/market/candles"
        s.api.get(path, done, params)

    def repair(self, pair):
        key = (pair, "repair")
        if self.service.closed or key in self.pending or pair not in self.repairs:
            return
        job = self.repairs[pair]
        cursor, generation, started = job["cursor"], self.generation, time.monotonic()
        self.pending.add(key)
        job.pop("failed", None)
        def done(rows, error):
            if generation != self.generation or self.service.closed:
                return
            self.pending.discard(key)
            try:
                if error:
                    raise ValueError(str(error))
                rows = [r for r in valid_candles(rows or []) if int(r[0]) < cursor]
                if not rows:
                    raise ValueError("未获取缺口数据")
            except (ValueError, TypeError, OverflowError):
                job["failed"] = True
                self.service.updated.emit("chart_status")
                return
            self.merge(pair, rows, started=started)
            job["cursor"] = int(rows[0][0])
            if job["cursor"] <= shift(job["target"], pair[1]):
                self.repairs.pop(pair, None)
                self.fetch(*pair)
            else:
                QTimer.singleShot(350, lambda: self.repair(pair) if generation == self.generation else None)
        self.service.api.get("/api/v5/market/history-candles", done,
            {"instId": pair[0], "bar": pair[1], "after": str(cursor), "limit": "300"})

    def ensure_display(self, owner, instrument, base, bar, left, count):
        if left is None:
            return
        pair = (instrument, bar)
        visible = count*BARS[base]/BARS[bar]
        self.set_view(owner, pair, left, visible)
        expected_view = self.view_windows[pair].get(owner)
        if self.service.running and pair not in self.extra_streams and pair != (self.service.selected, self.service.bar):
            self.activate()
        if time.monotonic()-self.display_refresh.get(pair, -1e9) >= 30:
            self.display_refresh[pair] = time.monotonic()
            self.fetch(*pair, _startup=True)
        key = (pair, "pyramid", int(left), int(count))
        if key in self.pending:
            return
        self.pending.add(key)
        generation, started = self.generation, time.monotonic()
        begin, end = shift(floor_time(max(0, left), bar), bar, -300), int(left+count*BARS[base]*1000)
        def loaded(rows, error):
            if generation != self.generation or self.service.closed:
                return
            self.pending.discard(key)
            if self.view_windows.get(pair, {}).get(owner) != expected_view:
                return
            if not error and rows:
                indexes = self.raw_index(pair)[2]
                derived = [row for row in rows if row[0] not in indexes or (pair, row[0]) in self.derived_stamps]
                if derived:
                    self.merge(pair, derived, persist=False, started=started)
                    self.derived_stamps.update((pair, row[0]) for row in derived if indexes.get(row[0]) == row)
            if not self.service.candles.get(pair):
                self.fetch(*pair)
            else:
                self.ensure_range(pair, left, visible, owner=owner)
        self.service.io.submit(lambda: self.service.market_cache.aggregate_range(instrument, base, bar, begin, end), loaded)

    def ensure_range(self, pair, left, count, *, owner=None):
        """恢复较早视口时优先读本地缓存，缺失部分才请求远端。"""
        rows = self.service.candles.get(pair, [])
        if not rows or left is None or self.service.closed:
            return
        self.set_view(pair if owner is None else owner, pair, left, count)
        interval = BARS[pair[1]]*1000
        begin = floor_time(max(0, left), pair[1], anchor=rows[0][0])
        end = min(int(rows[-1][0]), left+count*interval)
        stamps = self.raw_index(pair)[2]
        missing = next((t for t in openings(begin, end, pair[1], anchor=begin) if t not in stamps), None)
        if missing is None:
            return
        prefetch = min(2000, max(300, int(count)))
        cache_begin = max(0, shift(begin, pair[1], -prefetch))
        cache_end = shift(floor_time(end, pair[1], anchor=begin), pair[1], prefetch)
        key = (pair, "cache-window", int(begin), int(end))
        if key in self.pending:
            return
        self.pending.add(key)
        generation = self.generation
        def loaded(cached, error):
            if generation != self.generation or self.service.closed:
                return
            self.pending.discard(key)
            stamps = self.raw_index(pair)[2]
            if not error and cached:
                self.merge(pair, [row for row in cached if row[0] not in stamps], persist=False)
            self._fetch_range(pair, begin, end, interval)
        self.service.io.submit(lambda: self.service.market_cache.range(*pair, cache_begin, cache_end), loaded)

    def _fetch_range(self, pair, begin, end, interval):
        stamps = self.raw_index(pair)[2]
        missing = next((t for t in openings(begin, end, pair[1], anchor=begin) if t not in stamps), None)
        if missing is None:
            self.history_state[pair] = ""
            self.service.updated.emit("chart_status")
            return
        after = min(shift(missing, pair[1], 300), shift(floor_time(end, pair[1], anchor=begin), pair[1]))
        if len(self.window_attempts) > 512:
            cutoff = time.monotonic()-120
            self.window_attempts = {key: value for key, value in self.window_attempts.items() if value >= cutoff}
        key = (pair, "window", int(after))
        if key in self.pending or time.monotonic()-self.window_attempts.get(key, -1e9) < 30:
            return
        self.pending.add(key)
        self.window_attempts[key] = time.monotonic()
        self.history_state[pair] = "加载指定时间范围 K 线…"
        self.service.updated.emit("chart_status")
        generation, started = self.generation, time.monotonic()
        def done(data, error):
            if generation != self.generation or self.service.closed:
                return
            self.pending.discard(key)
            try:
                if error:
                    raise ValueError(str(error))
                data = valid_candles(data or [])
            except (ValueError, TypeError, OverflowError):
                self.history_state[pair] = "历史加载失败 · 点击重试"
                self.service.updated.emit("chart_status")
                return
            self.history_state[pair] = "" if data else "该时间范围无可用 K 线"
            self.merge(pair, data, started=started)
        self.service.api.get("/api/v5/market/history-candles", done,
            {"instId": pair[0], "bar": pair[1], "after": str(int(after)), "limit": "300"})

    def text(self, pair):
        fresh = time.time() - self.service.candle_times.get(pair, 0) <= 30
        status = (self.stream_state if pair == (self.service.selected, self.service.bar)
                  else self.pair_status.get(pair, "等待行情")) if fresh else "数据过期 / 等待同步"
        if pair in self.repairs:
            status += " · " + ("断线缺口待重试" if self.repairs[pair].get("failed") else "正在补齐断线缺口")
        return status + (" · " + self.history_state[pair] if self.history_state.get(pair) else "")

    def reset(self):
        self.generation += 1
        for series in self.series.values():
            series.cancel()
            series.deleteLater()
        self.series.clear()
        self.indicator_cache.clear()
        for token in self.indicator_pending.values():
            self.service.compute.cancel(token)
        self.indicator_pending.clear()
        self.raw_indexes.clear()
        self.recent_pairs = OrderedDict.fromkeys(self.service.candles)
        self.revisions.clear()
        self.pending.clear()
        self.history_state.clear()
        self.stream_versions.clear()
        self.repairs.clear()
        self.window_attempts.clear()
        self.view_windows.clear()
        self.display_refresh.clear()
        self.derived_stamps.clear()
        if self.stream:
            self.stream.close()
            self.stream.deleteLater()
            self.stream = None
        for stream in self.extra_streams.values():
            stream.close()
            stream.deleteLater()
        self.extra_streams.clear()
        self.pair_status.clear()
        self.stream_state = "重连中"

    def close(self):
        self.reset()
