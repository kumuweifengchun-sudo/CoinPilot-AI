"""控件属性不能覆盖 Qt 方法；指标编辑必须能创建并保存。"""
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QDialog

from coinpilot_ai.charts.dialogs import DrawingDialog
from coinpilot_ai.charts.settings import IndicatorEditDialog
from coinpilot_ai.charts.state import drawing
from coinpilot_ai.trading.panel import RiskDialog
from coinpilot_ai.charts.canvas import CandleChart
from coinpilot_ai.market.depth import MarketMicro
from coinpilot_ai.ui.common import AccountTabs
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget


@pytest.mark.parametrize("kind,params", [
    ("EMA", {"period": 20}),
    ("BOLL", {"period": 20, "deviations": 2}),
])
def test_indicator_editor_creates_and_saves_period_and_width(app, kind, params):
    item = {"id": "test", "kind": kind, "params": params, "bar": "chart",
            "color": "#35a4ff", "width": 1.5, "visible": True}
    dialog = IndicatorEditDialog(item)
    try:
        assert dialog.width() > 0
        dialog.params["period"].setValue(30)
        dialog.line_width.setValue(2.5)
        dialog.bar.setCurrentIndex(dialog.bar.findData("1H"))
        dialog.save()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.item["params"]["period"] == 30
        assert dialog.item["width"] == 2.5
        assert dialog.item["bar"] == "1H"
        assert item["params"]["period"] == 20
    finally:
        dialog.deleteLater()


def test_drawing_editor_preserves_qt_geometry_style_and_result(app):
    dialog = DrawingDialog(drawing("trend", [[60000, 100.], [120000, 110.]]))
    try:
        assert dialog.width() > 0
        assert dialog.style() is not None
        dialog.name.setText("测试趋势线")
        dialog.line_width.setValue(2.)
        dialog.line_style.setCurrentIndex(dialog.line_style.findData("dash"))
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.result_object["width"] == 2.
        assert dialog.result_object["style"] == "dash"
    finally:
        dialog.deleteLater()


def test_risk_editor_keeps_qdialog_result_callable_after_validation_error(app):
    service = SimpleNamespace(quotes={}, balance={}, specs={})
    dialog = RiskDialog(service, "BTC-USDT-SWAP", lambda *_: None)
    try:
        dialog.calculate()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.result_label.text()
        assert not dialog.use_button.isEnabled()
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


def test_chart_preserves_qwidget_position_methods(app):
    chart = CandleChart()
    try:
        chart.move(17, 23)
        assert chart.x() == 17
        assert chart.y() == 23
        assert chart.size().width() == chart.width()
    finally:
        chart.deleteLater()


def test_micro_socket_lifecycle_preserves_qobject_disconnect(app):
    class Service(QObject):
        updated = pyqtSignal(str)

    micro = MarketMicro(Service())
    calls = []
    try:
        connection = micro.changed.connect(lambda: calls.append(True))
        micro.changed.emit()
        assert calls == [True]
        assert micro.disconnect(connection)
        micro.stop()
        micro.changed.emit()
        assert calls == [True]
    finally:
        micro.deleteLater()


def test_account_tabs_support_both_qt_overloads_and_keywords(app):
    tabs = AccountTabs()
    try:
        assert tabs.addTab(QWidget(), "文本") == 0
        assert tabs.addTab(QWidget(), QIcon(), "图标") == 1
        assert tabs.addTab(QWidget(), icon=QIcon(), label="关键字") == 2
        assert [tabs.selector.itemText(i) for i in range(3)] == ["文本", "图标", "关键字"]
    finally:
        tabs.deleteLater()
