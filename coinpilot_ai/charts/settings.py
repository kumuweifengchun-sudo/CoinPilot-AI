"""指标搜索与参数编辑。保存前保持草稿，不影响当前图表。"""
from copy import deepcopy
from uuid import uuid4
from typing import TYPE_CHECKING, Protocol

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                             QSpinBox, QVBoxLayout, QMessageBox, QWidget)
from PyQt6.QtCore import Qt

from coinpilot_ai.ui.qt import require
from coinpilot_ai.indicators.catalog import BUILTIN_CATALOG, IndicatorCatalog
from coinpilot_ai.indicators.input_widgets import InputFields
from coinpilot_ai.indicators.validation import instance_from_record, instance_record
from coinpilot_ai.indicators.models import IndicatorInstance, SourceKind
from coinpilot_ai.charts.state import IndicatorSetting, normalize_indicators
from coinpilot_ai.charts.dialogs import ColorButton
from coinpilot_ai.market.intervals import BARS

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


class IndicatorSettingsBook(Protocol):
    catalog: IndicatorCatalog
    indicators: list[IndicatorSetting]
    indicator_error: str

    @property
    def service(self) -> "CockpitService | None": ...

    def save_indicators(self, items: object) -> None: ...


class IndicatorEditDialog(QDialog):
    def __init__(self, item: object, parent: QWidget | None = None, *, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.original = instance_from_record(item, catalog)
        self.definition = catalog.resolve(self.original.definition, self.original.definition_revision)
        self.item = normalize_indicators([item], catalog)[0]
        item = self.item
        self.setWindowTitle("编辑 " + self.definition.name)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.input_fields = InputFields(self.definition, self.original.inputs)
        self.params = self.input_fields.fields
        form.addRow("参数", self.input_fields)
        self.bar = QComboBox()
        self.bar.addItem("跟随图窗", "chart")
        if self.definition.source == "builtin":
            for bar in BARS:
                self.bar.addItem(bar, bar)
        self.bar.setCurrentIndex(max(0, self.bar.findData(self.original.timeframe)))
        form.addRow("K 线周期", self.bar)
        self.color = ColorButton(item["color"])
        self.line_width = QDoubleSpinBox()
        self.line_width.setRange(.5, 6)
        self.line_width.setSingleStep(.5)
        self.line_width.setValue(item["width"])
        self.visible = QCheckBox("显示")
        self.visible.setChecked(item["visible"])
        form.addRow("颜色", self.color)
        form.addRow("线宽", self.line_width)
        form.addRow("状态", self.visible)
        self.plot_fields: dict[str, tuple[QCheckBox, ColorButton, QDoubleSpinBox]] = {}
        for plot in self.definition.plots:
            style = self.original.plots[plot.id]
            shown = QCheckBox(plot.title)
            shown.setChecked(style.visible)
            shade = ColorButton(style.color)
            width = QDoubleSpinBox()
            width.setRange(.5, 6)
            width.setValue(style.width)
            self.plot_fields[plot.id] = shown, shade, width
            form.addRow(shown, shade)
            form.addRow(plot.title + " 线宽", width)
        layout.addLayout(form)
        self.feedback = QLabel()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self) -> None:
        try:
            record = instance_record(self.original)
            record.update(inputs=self.input_fields.values(), timeframe=self.bar.currentData(),
                          visible=self.visible.isChecked(), plots={
                key: {"visible": shown.isChecked(), "color": shade.text(), "width": width.value()}
                for key, (shown, shade, width) in self.plot_fields.items()})
            # 旧统一样式发生改变时应用到所有输出，逐输出样式仍可单独编辑。
            if self.color.text() != self.item["color"] or self.line_width.value() != self.item["width"]:
                record["plots"] = {key: {"visible": shown.isChecked(), "color": self.color.text(),
                                        "width": self.line_width.value()}
                                   for key, (shown, _, _) in self.plot_fields.items()}
            self.item = normalize_indicators([record], self.catalog)[0]
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()



class IndicatorSettingsDialog(QDialog):
    def __init__(self, book: IndicatorSettingsBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.book = book
        self.catalog = book.catalog
        self.service = book.service
        self.items = deepcopy(book.indicators)
        self.setWindowTitle("指标")
        self.resize(560, 460)
        root = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索指标…")
        self.search.textChanged.connect(self.filter_available)
        root.addWidget(self.search)
        self.category = QComboBox()
        for title, value in (("全部指标", "all"), ("内置", "builtin"), ("我的指标", "custom"), ("最近使用", "recent")):
            self.category.addItem(title, value)
        root.addWidget(self.category)
        lists = QHBoxLayout()
        self.available = QListWidget()
        self.category.currentIndexChanged.connect(self.reload_available)
        self.reload_available()
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
        imports = QHBoxLayout()
        for title, callback in (("创建指标", self.create_script), ("导入 Pine Script", self.import_script),
                                ("编辑代码", self.edit_script), ("管理所选定义", self.manage_definition)):
            button = QPushButton(title)
            button.setEnabled(self.service is not None)
            button.clicked.connect(callback)
            imports.addWidget(button)
        root.addLayout(imports)
        self.feedback = QLabel("已有 EMA 可继续在设置页编辑；新增指标与 EMA 使用同一 K 线数据。")
        if book.indicator_error:
            self.feedback.setText(book.indicator_error)
        root.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.refresh()

    def reload_available(self) -> None:
        self.available.clear()
        category = self.category.currentData()
        recent = self.service.indicator_library.recent() if self.service else ()
        for definition in sorted(self.catalog.list(), key=lambda value: value.name):
            if category == "builtin" and definition.source != "builtin" or category == "custom" and definition.source == "builtin":
                continue
            if category == "recent" and definition.id not in recent:
                continue
            self.available.addItem(definition.title)
            entry = require(self.available.item(self.available.count() - 1))
            entry.setData(Qt.ItemDataRole.UserRole, definition.id)
        self.filter_available(self.search.text())

    def create_script(self) -> None:
        self.open_editor(language="coinpilot")

    def import_script(self) -> None:
        self.open_editor(language="pine")

    def edit_script(self) -> None:
        selected = self.available.currentItem()
        if selected is not None:
            self.open_editor(identity=selected.data(Qt.ItemDataRole.UserRole))

    def open_editor(self, *, identity: str | None = None, language: SourceKind = "pine") -> None:
        if self.service is None:
            return
        from coinpilot_ai.indicators.editor import IndicatorEditor, EXAMPLE
        source = EXAMPLE
        if identity is not None:
            try:
                source = self.service.indicator_library.source(identity)
                language = self.catalog.resolve(identity).source
            except ValueError as exc:
                self.feedback.setText(str(exc))
                return
        def add(instance: IndicatorInstance) -> None:
            self.items.extend(normalize_indicators([instance_record(instance)], self.catalog))
            self.refresh()
        dialog = IndicatorEditor(self.service, add, self, source=source, identity=identity,
                                 language=language, apply_title="加入所选指标")
        dialog.exec()
        self.reload_available()

    def manage_definition(self) -> None:
        selected = self.available.currentItem()
        if selected is None or self.service is None:
            return
        identity = selected.data(Qt.ItemDataRole.UserRole)
        if not isinstance(identity, str) or not identity.startswith("custom."):
            self.feedback.setText("内置定义不可删除。")
            return
        counts = self.service.indicator_library.references(identity)
        if any(item.get("definition") == identity for item in self.items):
            counts["charts"] += 1
        if any(counts.values()):
            detail = "、".join(f"{key}={value}" for key, value in counts.items() if value)
            answer = QMessageBox.question(self, "指标仍被引用", detail + "。仅从列表隐藏并保留已有实例？")
            if answer == QMessageBox.StandardButton.Yes:
                self.service.indicator_library.hide(identity)
        elif QMessageBox.question(self, "删除指标", "删除此定义的所有已保存版本？") == QMessageBox.StandardButton.Yes:
            self.service.indicator_library.delete(identity, confirmed=True)
        self.reload_available()

    def filter_available(self, query):
        for index in range(self.available.count()):
            item = require(self.available.item(index))
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
        from PyQt6.QtCore import Qt
        definition = self.catalog.resolve(selected.data(Qt.ItemDataRole.UserRole))
        instance = IndicatorInstance(uuid4().hex, definition.id, definition.revision)
        self.items.extend(normalize_indicators([instance_record(instance)], self.catalog))
        self.refresh()
        self.selected.setCurrentRow(len(self.items)-1)

    def edit(self):
        index = self.selected.currentRow()
        if index < 0:
            return
        dialog = IndicatorEditDialog(self.items[index], self, catalog=self.catalog)
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
