"""EMA、绘图属性及对象管理窗口。"""
from copy import deepcopy
import math

from PyQt6.QtCore import QDateTime, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .chart_state import BARS, TOOLS, DEFAULT_EMAS, validate_emas
from .ui_common import button
from ..theme import color as theme_color


class ColorButton(QPushButton):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.set_color(value)
        self.clicked.connect(self.choose)

    def set_color(self, color):
        self.color = color
        self.setText(color)
        self.setStyleSheet(f"QPushButton {{color: {color}; padding: 4px;}}")

    def choose(self):
        color = QColorDialog.getColor(QColor(self.color), self, "选择颜色")
        if color.isValid():
            self.set_color(color.name())


def width_spin(value):
    widget = QDoubleSpinBox()
    widget.setRange(.5, 6)
    widget.setSingleStep(.5)
    widget.setValue(value)
    return widget


def dialog_buttons(dialog, layout):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)


class EmaDialog(QDialog):
    def __init__(self, book, parent=None):
        super().__init__(parent)
        self.book = book
        self.setWindowTitle("EMA 指标 · 所有币种与页面共用")
        self.resize(570, 360)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("以收盘价计算。周期 1—1000；* 表示历史预热不足。"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["显示", "周期", "颜色", "线宽", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        root.addWidget(self.table)
        row = QHBoxLayout()
        row.addWidget(button("添加 EMA", lambda: self.add({"period": 120, "color": theme_color("positive"), "width": 1.5, "visible": True})))
        row.addWidget(button("恢复默认 20 / 60", self.defaults))
        root.addLayout(row)
        for item in book.emas:
            self.add(item)
        dialog_buttons(self, root)

    def defaults(self):
        self.table.setRowCount(0)
        for item in DEFAULT_EMAS:
            self.add(item)

    def add(self, item):
        row = self.table.rowCount()
        self.table.insertRow(row)
        visible = QCheckBox()
        visible.setChecked(item["visible"])
        period = QSpinBox()
        period.setRange(1, 1000)
        period.setValue(item["period"])
        delete = button("删除", lambda: self.remove(delete))
        for col, widget in enumerate((visible, period, ColorButton(item["color"]), width_spin(item["width"]), delete)):
            self.table.setCellWidget(row, col, widget)
        self.table.setRowHeight(row, 38)

    def remove(self, widget):
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 4) is widget:
                self.table.removeRow(row)
                break

    def accept(self):
        items = []
        for row in range(self.table.rowCount()):
            items.append({"visible": self.table.cellWidget(row, 0).isChecked(), "period": self.table.cellWidget(row, 1).value(),
                          "color": self.table.cellWidget(row, 2).color, "width": self.table.cellWidget(row, 3).value()})
        self.book.save_emas(validate_emas(items))
        super().accept()


class DrawingDialog(QDialog):
    def __init__(self, obj, parent=None):
        super().__init__(parent)
        self.result_object = deepcopy(obj)
        self.setWindowTitle(TOOLS[obj["tool"]] + " · 属性")
        self.resize(520, 380 if obj["tool"] != "fib" else 620)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.color, self.width = ColorButton(obj["color"]), width_spin(obj["width"])
        self.style = QComboBox()
        for name, key in (("实线", "solid"), ("虚线", "dash"), ("点线", "dot")):
            self.style.addItem(name, key)
        self.style.setCurrentIndex(self.style.findData(obj["style"]))
        for title, widget in (("颜色", self.color), ("线宽", self.width), ("线型", self.style)):
            form.addRow(title, widget)
        self.text = QLineEdit(obj.get("text", ""))
        if obj["tool"] == "text":
            form.addRow("文字", self.text)
        self.anchors = []
        for i, (stamp, price) in enumerate(obj["anchors"]):
            row = QHBoxLayout()
            date = QDateTimeEdit(QDateTime.fromMSecsSinceEpoch(int(stamp)))
            date.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            date.setCalendarPopup(True)
            field = QLineEdit(f"{price:.12g}")
            row.addWidget(date)
            row.addWidget(field)
            form.addRow(f"锚点 {i+1}", row)
            self.anchors.append((date, field))
        root.addLayout(form)
        self.locked, self.hidden = QCheckBox("锁定"), QCheckBox("隐藏")
        self.locked.setChecked(obj["locked"])
        self.hidden.setChecked(obj["hidden"])
        row = QHBoxLayout()
        row.addWidget(self.locked)
        row.addWidget(self.hidden)
        row.addStretch()
        root.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("显示周期"))
        self.bars = {}
        for bar in BARS:
            check = QCheckBox(bar)
            check.setChecked(bar in obj["bars"])
            self.bars[bar] = check
            row.addWidget(check)
        root.addLayout(row)
        self.levels = None
        if obj["tool"] == "fib":
            self.levels = QTableWidget(0, 3)
            self.levels.setHorizontalHeaderLabels(["比例", "标签", "颜色"])
            self.levels.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            root.addWidget(self.levels, 1)
            for level in obj["levels"]:
                self.add_level(level)
            row = QHBoxLayout()
            row.addWidget(button("添加比例", lambda: self.add_level({"value": 1.618, "label": "1.618", "color": self.color.color})))
            row.addWidget(button("删除所选比例", lambda: self.levels.removeRow(self.levels.currentRow())))
            root.addLayout(row)
        self.error = QLabel()
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        dialog_buttons(self, root)

    def add_level(self, level):
        row = self.levels.rowCount()
        self.levels.insertRow(row)
        self.levels.setItem(row, 0, QTableWidgetItem(str(level["value"])))
        self.levels.setItem(row, 1, QTableWidgetItem(level["label"]))
        self.levels.setCellWidget(row, 2, ColorButton(level["color"]))
        self.levels.setRowHeight(row, 34)

    def accept(self):
        try:
            anchors = [[date.dateTime().toMSecsSinceEpoch(), float(field.text())] for date, field in self.anchors]
            if any(not math.isfinite(a[1]) or abs(a[1]) > 1e20 for a in anchors):
                raise ValueError("锚点价格必须是有限数值")
            levels = []
            if self.levels is not None:
                for row in range(self.levels.rowCount()):
                    value = float(self.levels.item(row, 0).text())
                    if not math.isfinite(value) or abs(value) > 1000:
                        raise ValueError("比例必须是 -1000 至 1000 的有限数值")
                    levels.append({"value": value, "label": self.levels.item(row, 1).text(), "color": self.levels.cellWidget(row, 2).color})
                if not levels:
                    raise ValueError("至少保留一条斐波拉契比例")
            if self.result_object["locked"] and self.locked.isChecked():
                # 锁定对象允许显隐和解锁，禁止悄悄改变锚点/外观。
                self.result_object["hidden"] = self.hidden.isChecked()
            else:
                self.result_object.update(anchors=anchors, color=self.color.color, width=self.width.value(),
                    style=self.style.currentData(), text=self.text.text(), locked=self.locked.isChecked(), hidden=self.hidden.isChecked(),
                    bars=[bar for bar, check in self.bars.items() if check.isChecked()])
                if self.levels is not None:
                    self.result_object["levels"] = levels
        except (ValueError, AttributeError) as exc:
            self.error.setText(str(exc) or "请检查锚点及比例")
            return
        super().accept()


class ObjectsDialog(QDialog):
    def __init__(self, chart, parent=None):
        super().__init__(parent)
        self.chart = chart
        self.setWindowTitle("绘图对象 · " + chart.instrument)
        self.resize(480, 400)
        root = QVBoxLayout(self)
        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(lambda _: self.edit())
        root.addWidget(self.items)
        row = QHBoxLayout()
        for text, callback in (("属性", self.edit), ("显隐", lambda: self.toggle("hidden")), ("锁定 / 解锁", lambda: self.toggle("locked")), ("删除", self.delete)):
            row.addWidget(button(text, callback))
        root.addLayout(row)
        root.addWidget(QLabel("锁定对象须先解锁再删除；画线在同币种的各周期及两个页面共享。"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.reload()

    def reload(self):
        current = self.identity()
        self.items.clear()
        for obj in self.chart.objects:
            text = TOOLS[obj["tool"]] + (" · "+obj["text"] if obj["text"] else "")
            text += " · " + ("隐藏" if obj["hidden"] else "显示") + (" · 已锁定" if obj["locked"] else "")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, obj["id"])
            self.items.addItem(item)
            if obj["id"] == current:
                self.items.setCurrentItem(item)

    def identity(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def edit(self):
        edit_drawing(self.chart, self.identity(), self)
        self.reload()

    def toggle(self, name):
        obj = next((o for o in self.chart.objects if o["id"] == self.identity()), None)
        if obj:
            obj[name] = not obj[name]
            self.chart.persist_objects()
            self.reload()

    def delete(self):
        self.chart.selected_id = self.identity()
        self.chart.delete_selected()
        self.reload()


def edit_drawing(chart, identity, parent):
    obj = next((o for o in chart.objects if o["id"] == identity), None)
    if obj:
        dialog = DrawingDialog(obj, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chart.objects = [dialog.result_object if o["id"] == identity else o for o in chart.objects]
            chart.persist_objects()
