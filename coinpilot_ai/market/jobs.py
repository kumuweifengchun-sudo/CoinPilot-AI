"""后台 I/O 与计算队列；结果只在所属 Qt 线程发布。"""
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from PyQt6.QtCore import QObject, pyqtSignal, Qt


class WorkQueue(QObject):
    completed = pyqtSignal(int, object, object)

    def __init__(self, parent=None, *, workers=1):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="candle-worker")
        self.callbacks = {}
        self.futures = {}
        self.serial = count()
        self.closed = False
        # PyQt 类型声明未包含连接类型参数，运行时保留跨线程排队投递。
        self.completed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)  # pyright: ignore[reportCallIssue]

    def submit(self, operation, callback):
        if self.closed:
            return None
        token = next(self.serial)
        self.callbacks[token] = callback
        future = self.executor.submit(operation)
        self.futures[token] = future

        def done(result):
            if result.cancelled():
                value, error = None, RuntimeError("任务已取消")
            else:
                try:
                    value, error = result.result(), None
                except Exception as exc:
                    value, error = None, exc
            self.completed.emit(token, value, error)
        future.add_done_callback(done)
        return token

    def _deliver(self, token, value, error):
        self.futures.pop(token, None)
        callback = self.callbacks.pop(token, None)
        if callback is not None and not self.closed:
            callback(value, error)
        elif hasattr(value, "close"):
            value.close()

    def cancel(self, token):
        self.callbacks.pop(token, None)
        future = self.futures.get(token)
        if future is not None:
            future.cancel()

    def close(self):
        self.closed = True
        self.callbacks.clear()
        self.executor.shutdown(wait=True, cancel_futures=True)
        for future in self.futures.values():
            if future.done() and not future.cancelled() and future.exception() is None:
                value = future.result()
                if hasattr(value, "close"):
                    value.close()
        self.futures.clear()
