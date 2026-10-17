"""共享样式必须落实到新建页面、弹窗与表单，而非仅修改样式常量。"""
import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from coinpilot_ai.ui.theme import DEFAULT_THEME_ID, LIGHT_THEME_ID, activate_theme


@pytest.mark.parametrize('theme', [DEFAULT_THEME_ID, LIGHT_THEME_ID])
@pytest.mark.parametrize('kind, margin', [('page', 8), ('dialog', 16), ('embedded', 8)])
def test_new_layouts_use_shared_margins_and_visible_gaps(
        app: QApplication, theme: str, kind: str, margin: int) -> None:
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    activate_theme(theme)
    parent = QWidget()
    host = QWidget(parent) if kind == 'page' else QDialog(parent)
    if kind == 'embedded':
        host.setWindowFlags(Qt.WindowType.Widget)
    layout = QVBoxLayout(host)
    first, second = QLineEdit(), QLineEdit()
    layout.addWidget(first)
    layout.addWidget(second)
    layout.addStretch()
    host.resize(320, 200)
    parent.resize(360, 240)
    parent.show()
    host.show()
    app.processEvents()
    try:
        margins = layout.contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (margin,) * 4
        assert first.x() == margin and first.y() == margin
        assert second.y() - first.geometry().bottom() - 1 == 8
        # 明确的画布/结构容器例外不会被全局样式强行覆盖。
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        app.processEvents()
        assert first.x() == 0 and first.y() == 0
        assert second.y() - first.geometry().bottom() - 1 == 4
    finally:
        host.close()
        parent.close()
        parent.deleteLater()
        activate_theme(DEFAULT_THEME_ID)


def test_nested_form_inherits_spacing_without_extra_outer_padding(app: QApplication) -> None:
    host = QWidget()
    root = QVBoxLayout(host)
    form = QFormLayout()
    first, second = QLineEdit(), QLineEdit()
    form.addRow(QLabel('第一行'), first)
    form.addRow(QLabel('第二行'), second)
    root.addLayout(form)
    root.addStretch()
    host.resize(320, 200)
    host.show()
    app.processEvents()
    try:
        assert first.y() == 8
        assert second.y() - first.geometry().bottom() - 1 == 8
        assert form.horizontalSpacing() == 8
        assert form.contentsMargins().isNull()
    finally:
        host.close()
        host.deleteLater()
