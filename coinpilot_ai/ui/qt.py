"""收窄 Qt 创建或取回的必需对象，保留 Qt 的可空返回契约。"""
from typing import TypeVar

from PyQt6.QtWidgets import QApplication

T = TypeVar("T")


def require(value: T | None) -> T:
    if value is None:
        raise RuntimeError("所需对象不存在")
    return value


def application() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        raise RuntimeError("必须先创建 QApplication")
    return app
