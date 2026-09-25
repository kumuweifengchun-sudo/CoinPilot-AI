"""指标搜索与参数编辑。保存前保持草稿，不影响当前图表。"""
from copy import deepcopy
from uuid import uuid4

from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                             QSpinBox, QVBoxLayout)

from coinpilot_ai.market.indicators import DEFAULTS, KINDS, validate_spec
from coinpilot_ai.market.intervals import BARS


class IndicatorEditDialog(QDialog):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = deepcopy(item)
        self.setWindowTitle("编辑 " + item["kind"])
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.params = {}
        for key, value in item["params"].items():
            if key in ("deviations", "multiplier"):
                field = QDoubleSpinBox()
                field.setRange(.01, 100)
                field.setDecimals(2)
            else:
                field = QSpinBox()
                field.setRange(1, 1000)
            field.setValue(value)
            self.params[key] = field
            form.addRow(key, field)
        self.bar = QComboBox()
        self.bar.addItem("跟随图窗", "chart")
        for bar in BARS:
            self.bar.addItem(bar, bar)
        self.bar.setCurrentIndex(max(0, self.bar.findData(item.get("bar", "chart"))))
        form.addRow("K 线周期", self.bar)
        self.color = QPushButton(item["color"])
        self.color.clicked.connect(self.choose_color)
        self.width = QDoubleSpinBox()
        self.width.setRange(.5, 6)
        self.width.setSingleStep(.5)
        self.width.setValue(item["width"])
        self.visible = QCheckBox("显示")
        self.visible.setChecked(item["visible"])
        form.addRow("颜色", self.color)
        form.addRow("线宽", self.width)
        form.addRow("状态", self.visible)
        layout.addLayout(form)
        self.feedback = QLabel()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_color(self):
        chosen = QColorDialog.getColor(parent=self)
        if chosen.isValid():
            self.color.setText(chosen.name())

    def save(self):
        self.item.update(params={key: widget.value() for key, widget in self.params.items()},
                         bar=self.bar.currentData(), color=self.color.text(),
                         width=self.width.value(), visible=self.visible.isChecked())
        try:
            validate_spec(self.item)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()


class IndicatorSettingsDialog(QDialog):
    def __init__(self, book, parent=None):
        super().__init__(parent)
        self.book = book
        self.items = deepcopy(book.indicators)
        self.setWindowTitle("指标")
        self.resize(560, 460)
        root = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索指标…")
        self.search.textChanged.connect(self.filter_available)
        root.addWidget(self.search)
        lists = QHBoxLayout()
        self.available = QListWidget()
        self.available.addItems(sorted(KINDS))
        self.available.itemDoubleClicked.connect(lambda _: self.add())
        self.selected = QListWidget()
        self.selected.itemDoubleClicked.connect(lambda _: self.edit())
        lists.addWidget(self.available)
        lists.addWidget(self.selected)
        root.addLayout(lists, 1)
        controls = QHBoxLayout()
        for title, callback in (("添加", self.add), ("编辑参数", self.edit), ("显示／隐藏", self.toggle), ("删除", self.remove)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            controls.addWidget(button)
        root.addLayout(controls)
        self.feedback = QLabel("已有 EMA 可继续在设置页编辑；新增指标与 EMA 使用同一 K 线数据。")
        root.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.refresh()

    def filter_available(self, query):
        for index in range(self.available.count()):
            item = self.available.item(index)
            item.setHidden(query.strip().lower() not in item.text().lower())

    def refresh(self):
        self.selected.clear()
        for item in self.items:
            self.selected.addItem(("● " if item["visible"] else "○ ") + item["kind"] + " " +
                                  ", ".join(f"{key}={value}" for key, value in item["params"].items()))

    def add(self):
        selected = self.available.currentItem()
        if selected is None:
            return
        kind = selected.text()
        self.items.append({"id": uuid4().hex, "kind": kind, "params": dict(DEFAULTS[kind]),
                           "color": "#35a4ff", "width": 1.5, "visible": True})
        self.refresh()
        self.selected.setCurrentRow(len(self.items)-1)

    def edit(self):
        index = self.selected.currentRow()
        if index < 0:
            return
        dialog = IndicatorEditDialog(self.items[index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.items[index] = dialog.item
            self.refresh()
            self.selected.setCurrentRow(index)

    def toggle(self):
        index = self.selected.currentRow()
        if index >= 0:
            self.items[index]["visible"] = not self.items[index]["visible"]
            self.refresh()
            self.selected.setCurrentRow(index)

    def remove(self):
        index = self.selected.currentRow()
        if index >= 0:
            self.items.pop(index)
            self.refresh()

    def save(self):
        try:
            self.book.save_indicators(self.items)
        except (ValueError, KeyError) as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()
