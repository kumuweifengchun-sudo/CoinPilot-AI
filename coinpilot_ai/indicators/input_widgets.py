"""按 InputDefinition 构建参数表，图表、提醒和研究共用。"""
from collections.abc import Mapping

from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QSpinBox, QWidget

from .models import IndicatorDefinition, InputValue
from .validation import input_value, validate_inputs

type InputWidget = QSpinBox | QDoubleSpinBox | QCheckBox | QComboBox


class InputFields(QWidget):
    def __init__(self, definition: IndicatorDefinition, values: Mapping[str, object] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.definition = definition
        self.fields: dict[str, InputWidget] = {}
        form = QFormLayout(self)
        normalized = validate_inputs(definition, values or {})
        for item in definition.inputs:
            value = normalized[item.id]
            widget: InputWidget
            if item.type == "int":
                widget = QSpinBox()
                widget.setRange(int(item.minimum if item.minimum is not None else -2147483647),
                                int(item.maximum if item.maximum is not None else 2147483647))
                if item.step is not None:
                    widget.setSingleStep(int(item.step))
                if isinstance(value, (int, float)):
                    widget.setValue(int(value))
            elif item.type == "float":
                widget = QDoubleSpinBox()
                widget.setDecimals(6)
                widget.setRange(item.minimum if item.minimum is not None else -1e12,
                                item.maximum if item.maximum is not None else 1e12)
                if item.step is not None:
                    widget.setSingleStep(item.step)
                if isinstance(value, (int, float)):
                    widget.setValue(float(value))
            elif item.type == "bool":
                widget = QCheckBox()
                widget.setChecked(bool(value))
            else:
                widget = QComboBox()
                for source in ("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4"):
                    widget.addItem(source, source)
                widget.setCurrentIndex(max(0, widget.findData(value)))
            self.fields[item.id] = widget
            form.addRow(item.title, widget)

    def values(self) -> dict[str, InputValue]:
        result: dict[str, InputValue] = {}
        for item in self.definition.inputs:
            widget = self.fields[item.id]
            if isinstance(widget, QCheckBox):
                raw: object = widget.isChecked()
            elif isinstance(widget, QComboBox):
                raw = widget.currentData()
            else:
                raw = widget.value()
            result[item.id] = input_value(item, raw)
        return validate_inputs(self.definition, result)

    def set_values(self, values: Mapping[str, object]) -> None:
        normalized = validate_inputs(self.definition, values)
        for item in self.definition.inputs:
            widget, value = self.fields[item.id], normalized[item.id]
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif isinstance(value, (int, float)):
                if isinstance(widget, QSpinBox):
                    widget.setValue(int(value))
                else:
                    widget.setValue(float(value))
