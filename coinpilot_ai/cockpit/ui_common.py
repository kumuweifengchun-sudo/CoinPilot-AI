"""工作台共用控件。"""
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                            QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout)
from ..icons import set_button_icon
from ..theme import style_sheet

STYLE = style_sheet()


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


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.setShowGrid(False)
    widget.verticalHeader().setDefaultSectionSize(34)
    widget.setWordWrap(False)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setMinimumHeight(110)
    return widget


def fill_table(widget, rows):
    selected_ids = {widget.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in widget.selectionModel().selectedRows() if widget.item(index.row(), 0)}
    widget.setUpdatesEnabled(False)
    widget.setRowCount(len(rows))
    for i, (identity, values) in enumerate(rows):
        for j, value in enumerate(values):
            item = QTableWidgetItem(str(value))
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
