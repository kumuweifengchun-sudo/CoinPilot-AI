"""在真实 Qt 控件上验证分隔点绘制、主题切换和原生拖动。"""
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QHoverEvent, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDockWidget, QLabel, QMainWindow, QSplitter

from coinpilot_ai.ui.qt import require
from coinpilot_ai.ui.theme import DEFAULT_THEME_ID, LIGHT_THEME_ID, activate_theme, color


@pytest.mark.parametrize("theme", [DEFAULT_THEME_ID, LIGHT_THEME_ID])
@pytest.mark.parametrize("orientation", [Qt.Orientation.Horizontal, Qt.Orientation.Vertical])
def test_splitter_grip_and_native_resize(app, theme, orientation):
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    activate_theme(theme)
    splitter = QSplitter(orientation)
    splitter.addWidget(QLabel("左 / 上"))
    splitter.addWidget(QLabel("右 / 下"))
    splitter.resize(400, 300)
    splitter.show()
    app.processEvents()
    try:
        handle = require(splitter.handle(1))
        QTest.mouseMove(splitter.widget(0), QPoint(10, 10))
        # offscreen 创建同位置窗口时可能保留上一窗口的 hover，显式送出离开事件。
        app.sendEvent(handle, QHoverEvent(QEvent.Type.HoverLeave, QPointF(-1, -1),
                                         QPointF(-1, -1), QPointF(0, 0)))
        app.processEvents()
        image = handle.grab().toImage()
        center = image.rect().center()
        assert image.pixelColor(0, 0).name() == color("background").lower()
        assert image.pixelColor(center).name() != color("background").lower()
        QTest.mouseMove(handle, handle.rect().center())
        center_position = QPointF(handle.rect().center())
        app.sendEvent(handle, QHoverEvent(QEvent.Type.HoverEnter, center_position,
                                         QPointF(handle.mapToGlobal(handle.rect().center())),
                                         QPointF(-1, -1)))
        app.processEvents()
        assert handle.grab().toImage().pixelColor(0, 0).name() == color("surface_hover").lower()
        before = splitter.sizes()
        point = handle.rect().center()
        delta = QPoint(40, 0) if orientation == Qt.Orientation.Horizontal else QPoint(0, 40)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(handle, point + delta)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        app.processEvents()
        assert splitter.sizes()[0] > before[0]
        state = splitter.saveState()
        saved = splitter.sizes()
        splitter.setSizes([100, 100])
        assert splitter.restoreState(state)
        assert splitter.sizes() == saved
    finally:
        splitter.close()
        splitter.deleteLater()
        activate_theme(DEFAULT_THEME_ID)


@pytest.mark.parametrize("theme", [DEFAULT_THEME_ID, LIGHT_THEME_ID])
@pytest.mark.parametrize("orientation", [Qt.Orientation.Horizontal, Qt.Orientation.Vertical])
def test_dock_separator_uses_same_grip(app, theme, orientation):
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    activate_theme(theme)
    host = QMainWindow()
    first, second = QDockWidget("模块一", host), QDockWidget("模块二", host)
    for dock in (first, second):
        dock.setWidget(QLabel("离线示例"))
        host.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    host.splitDockWidget(first, second, orientation)
    host.resize(400, 300)
    host.show()
    app.processEvents()
    try:
        QTest.mouseMove(first, QPoint(10, 10))
        app.processEvents()
        if orientation == Qt.Orientation.Horizontal:
            point = QPoint((first.geometry().right() + second.x()) // 2, host.height() // 2)
            edge = QPoint(point.x(), 2)
            delta = QPoint(40, 0)
        else:
            point = QPoint(host.width() // 2, (first.geometry().bottom() + second.y()) // 2)
            edge = QPoint(2, point.y())
            delta = QPoint(0, 40)
        image = host.grab().toImage()
        assert image.pixelColor(edge).name() == color("background").lower()
        assert image.pixelColor(point).name() != color("background").lower()
        QTest.mouseMove(host, point)
        app.processEvents()
        assert host.grab().toImage().pixelColor(edge).name() == color("surface_hover").lower()
        before = first.size()
        QTest.mousePress(host, Qt.MouseButton.LeftButton, pos=point)
        # offscreen 的 QTest.mouseMove 不携带按键；主窗口按 buttons 判断分隔拖动。
        app.sendEvent(host, QMouseEvent(QEvent.Type.MouseMove, QPointF(point + delta),
                      QPointF(host.mapToGlobal(point + delta)), Qt.MouseButton.NoButton,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        app.processEvents()
        QTest.mouseRelease(host, Qt.MouseButton.LeftButton, pos=point + delta)
        app.processEvents()
        assert first.size() != before
    finally:
        host.close()
        host.deleteLater()
        activate_theme(DEFAULT_THEME_ID)
