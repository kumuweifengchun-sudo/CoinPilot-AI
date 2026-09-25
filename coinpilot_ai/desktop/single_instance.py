"""进程锁先于配置和网络服务创建；重复启动仅唤回当前用户的已有实例。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from PyQt6.QtCore import QLockFile, QObject, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    def __init__(self, parent=None, *, namespace="desktop"):
        super().__init__(parent)
        identity = os.path.normcase(str(Path.home().resolve())) + ":" + namespace
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        # 与改名前的版本共用 IPC 和锁，防止同时运行并争用同一份用户数据。
        self.name = "crypto-widget-" + digest
        self.lock = QLockFile(str(Path(tempfile.gettempdir()) / (self.name + ".lock")))
        self.lock.setStaleLockTime(0)  # 活着的长时间运行实例不能因锁文件年龄被抢占。
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self.primary = False
        self.handler = None
        self.pending = []
        self.connections = set()

    def acquire(self, command="show"):
        if self.lock.tryLock(0):
            self.primary = True
            QLocalServer.removeServer(self.name)  # 只有持锁者可以清理崩溃遗留端点。
            if not self.server.listen(self.name):
                self.close()
                raise OSError("无法建立程序唤回通道，请稍后重新启动。")
            return True
        # 首实例可能刚获得锁、尚未完成监听；不再初始化任何业务或热键。
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(200):
                socket.write(json.dumps({"command": command}).encode() + b"\n")
                socket.flush()
                response = bytearray()
                while time.monotonic() < deadline:
                    if socket.bytesAvailable() or socket.waitForReadyRead(200):
                        response.extend(bytes(socket.readAll()))
                        if b"ok\n" in response:
                            socket.disconnectFromServer()
                            return False
                socket.abort()
                break
            socket.abort()
            time.sleep(.05)
        raise OSError("程序已在运行，但暂时未响应唤回。请从系统托盘打开，或稍后再试。")

    def set_handler(self, handler):
        self.handler = handler
        for command in self.pending:
            QTimer.singleShot(0, lambda c=command: handler(c))
        self.pending.clear()

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.connections.add(socket)
            socket.setReadBufferSize(4096)
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._drop(s))
            timeout = QTimer(socket)
            timeout.setSingleShot(True)
            timeout.timeout.connect(socket.abort)
            timeout.start(3000)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket):
        if not socket.canReadLine():
            if socket.bytesAvailable() >= 4096:
                socket.abort()
            return
        try:
            data = json.loads(bytes(socket.readLine()))
            command = data.get("command") if isinstance(data, dict) else None
            if command not in ("show", "settings", "workbench"):
                raise ValueError()
        except (ValueError, UnicodeError):
            socket.abort()
            return
        socket.write(b"ok\n")
        socket.flush()
        socket.disconnectFromServer()
        if self.handler is not None:
            QTimer.singleShot(0, lambda: self.handler(command))
        elif command not in self.pending:
            self.pending.append(command)

    def _drop(self, socket):
        self.connections.discard(socket)
        socket.deleteLater()

    def close(self):
        for socket in list(self.connections):
            socket.abort()
        self.server.close()
        if self.primary:
            self.lock.unlock()
            self.primary = False
