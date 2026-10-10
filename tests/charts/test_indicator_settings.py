"""实际点击参数编辑入口，验证模态编辑、取消及持久化。"""
from copy import deepcopy
from pathlib import Path
from typing import Literal

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPushButton, QSpinBox

from coinpilot_ai.charts.settings import IndicatorEditDialog, IndicatorSettingsDialog
from coinpilot_ai.charts.state import ChartBook
from coinpilot_ai.core.store import Store
from coinpilot_ai.ui.qt import require


@pytest.mark.parametrize("entry", ["button", "double_click"])
@pytest.mark.parametrize("accept", [True, False])
def test_indicator_edit_entry_opens_modal_and_preserves_draft(
    app: QApplication, tmp_path: Path, entry: Literal["button", "double_click"], accept: bool,
) -> None:
    store = Store(tmp_path / "indicators.sqlite3")
    book = ChartBook(store)
    book.save_indicators([
        {"id": "ema20", "kind": "EMA", "params": {"period": 20}},
        {"id": "ema50", "kind": "EMA", "params": {"period": 50}},
    ])
    before = deepcopy(book.indicators)
    settings = IndicatorSettingsDialog(book)
    editors: list[IndicatorEditDialog] = []
    failures: list[str] = []

    def finish_editor() -> None:
        modal = app.activeModalWidget()
        if not isinstance(modal, IndicatorEditDialog):
            failures.append("参数编辑入口没有打开编辑对话框")
            settings.reject()
            return
        editors.append(modal)
        period = modal.params["period"]
        if not isinstance(period, QSpinBox):
            failures.append("EMA 周期控件类型错误")
            modal.reject()
            return
        period.setValue(30)
        modal.line_width.setValue(2.5)
        buttons = require(modal.findChild(QDialogButtonBox))
        role = QDialogButtonBox.StandardButton.Save if accept else QDialogButtonBox.StandardButton.Cancel
        QTest.mouseClick(require(buttons.button(role)), Qt.MouseButton.LeftButton)

    try:
        settings.show()
        app.processEvents()
        settings.selected.setCurrentRow(1)
        selected = require(settings.selected.item(1))
        position = settings.selected.visualItemRect(selected).center()
        QTest.mouseClick(settings.selected.viewport(), Qt.MouseButton.LeftButton, pos=position)
        QTimer.singleShot(0, finish_editor)
        if entry == "button":
            button = next(button for button in settings.findChildren(QPushButton) if button.text() == "编辑参数")
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        else:
            QTest.mouseDClick(settings.selected.viewport(), Qt.MouseButton.LeftButton, pos=position)
        app.processEvents()
        assert not failures
        assert len(editors) == 1
        editor = editors[0]
        assert editor.width() > 0
        assert editor.result() == (QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected)
        assert settings.selected.currentRow() == 1
        assert settings.items[0] == before[0]
        assert settings.items[1]["params"]["period"] == (30 if accept else 50)
        assert settings.items[1]["width"] == (2.5 if accept else before[1]["width"])
        assert book.indicators == before
        if accept:
            settings.save()
            assert settings.result() == QDialog.DialogCode.Accepted
            assert ChartBook(store).indicators == settings.items
        else:
            settings.reject()
            assert ChartBook(store).indicators == before
    finally:
        settings.close()
        settings.deleteLater()
        store.close()
