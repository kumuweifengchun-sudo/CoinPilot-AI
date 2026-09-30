"""工作台共用控件。"""
from datetime import datetime
from typing import overload
from PyQt6.QtGui import QIcon

from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                            QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)
from coinpilot_ai.ui.qt import require
from .icons import set_button_icon
from .theme import style_sheet

STYLE = style_sheet()


def combo(items):
    widget = QComboBox()
    for value, label in items:
        widget.addItem(label, value)
    return widget


def select_data(widget, value):
    index = widget.findData(value)
    widget.setCurrentIndex(max(0, index))


def button(text, callback, primary=False):
    widget = QPushButton(text)
    for terms, name in ((('添加', '新建', '新增', '＋'), 'plus'), (('删除', '移除'), 'delete'),
                        (('保存',), 'save'), (('复制',), 'copy'), (('同步', '刷新', '重试', '查询'), 'refresh-cw'),
                        (('生成', '追问', '草稿'), 'sparkles'), (('预览',), 'eye'),
                        (('检查并确认',), 'shield-check'), (('恢复',), 'rotate-ccw')):
        if any(term in text for term in terms):
            set_button_icon(widget, name, color="@accent_text" if primary else None)
            break
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(lambda checked=False: callback())
    return widget


class AccountTabs(QTabWidget):
    """窄面板用分类选择器，避免标签挤压与不可辨认的滚动箭头。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selector = QComboBox()
        self.selector.setAccessibleName('账户信息分类')
        self.selector.hide()
        self.selector.currentIndexChanged.connect(self.setCurrentIndex)
        self.currentChanged.connect(self._selected)

    @overload
    def addTab(self, widget: QWidget | None, a1: str | None) -> int: ...

    @overload
    def addTab(self, widget: QWidget | None, icon: QIcon, label: str | None) -> int: ...

    def addTab(self, widget: QWidget | None, *args, **kwargs) -> int:
        # SIP 原生重载只接受位置参数；在 Python 层保留声明中的关键字调用契约。
        def add_text(a1: str | None) -> int:
            return super(AccountTabs, self).addTab(widget, a1)

        def add_icon(icon: QIcon, label: str | None) -> int:
            return super(AccountTabs, self).addTab(widget, icon, label)

        if "icon" in kwargs or (args and isinstance(args[0], QIcon)):
            index = add_icon(*args, **kwargs)
        else:
            index = add_text(*args, **kwargs)
        with QSignalBlocker(self.selector):
            self.selector.addItem(self.tabText(index))
            self.selector.setCurrentIndex(self.currentIndex())
        self._fit()
        return index

    def _selected(self, index):
        with QSignalBlocker(self.selector):
            self.selector.setCurrentIndex(index)

    def _fit(self):
        required = sum(self.fontMetrics().horizontalAdvance(self.tabText(i)) + 36 for i in range(self.count()))
        compact = self.width() < required
        require(self.tabBar()).setVisible(not compact)
        self.selector.setVisible(compact)

    def resizeEvent(self, a0):
        event = require(a0)
        super().resizeEvent(event)
        self._fit()


def table(headers, *, readable=False):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.setShowGrid(False)
    require(widget.verticalHeader()).setDefaultSectionSize(34)
    widget.setWordWrap(False)
    require(widget.verticalHeader()).hide()
    header = require(widget.horizontalHeader())
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive if readable else QHeaderView.ResizeMode.Stretch)
    if readable:
        header.setMinimumSectionSize(80)
        header.setStretchLastSection(True)
        for index, text in enumerate(headers):
            width = max(100, widget.fontMetrics().horizontalAdvance(text) + 32)
            widget.setColumnWidth(index, max(width, 155) if text == '合约' else width)
        widget.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    widget.setMinimumHeight(110)
    return widget


def fill_table(widget, rows):
    selected_ids = {widget.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in require(widget.selectionModel()).selectedRows() if widget.item(index.row(), 0)}
    widget.setUpdatesEnabled(False)
    widget.setRowCount(len(rows))
    for i, (identity, values) in enumerate(rows):
        for j, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            if j == 0:
                item.setData(Qt.ItemDataRole.UserRole, identity)
            widget.setItem(i, j, item)
        if identity in selected_ids:
            for j in range(widget.columnCount()):
                widget.item(i, j).setSelected(True)
    widget.setUpdatesEnabled(True)


def selected_id(widget):
    row = widget.currentRow()
    item = widget.item(row, 0) if row >= 0 else None
    return item.data(Qt.ItemDataRole.UserRole) if item else None


def timestamp(value):
    return datetime.fromtimestamp(float(value)).strftime("%m-%d %H:%M:%S") if value else "—"


def show_text(parent, title, text):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(760, 540)
    layout = QVBoxLayout(dialog)
    editor = QTextEdit()
    editor.setReadOnly(True)
    editor.setPlainText(text)
    layout.addWidget(editor)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def confirm(parent, title, text):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Icon.Question)
    yes = dialog.addButton("确认", QMessageBox.ButtonRole.AcceptRole)
    no = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(no)
    dialog.exec()
    return dialog.clickedButton() == yes
