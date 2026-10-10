"""提醒规则与指标条件编辑器，供工作台设置和策略研究共用。"""
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QSpinBox, QVBoxLayout, QWidget

from coinpilot_ai.indicators.catalog import BUILTIN_CATALOG, IndicatorCatalog
from coinpilot_ai.indicators.conditions import spec_title, validate_condition
from coinpilot_ai.indicators.input_widgets import InputFields
from coinpilot_ai.indicators.models import IndicatorDefinition
from coinpilot_ai.indicators.validation import runtime_instance
from coinpilot_ai.ui.common import button, combo, select_data
from .alerts import BARS, METRICS


class IndicatorConditionDialog(QDialog):
    def __init__(self, value: dict[str, object] | None = None, parent: QWidget | None = None,
                 *, catalog: IndicatorCatalog = BUILTIN_CATALOG) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.condition: dict[str, object] | None = None
        self.setWindowTitle("指标条件")
        self.resize(500, 520)
        value = value or {}
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.bar = combo([(bar, bar) for bar in BARS])
        select_data(self.bar, value.get("bar", "15m"))
        self.kind = QComboBox()
        self.compare_kind = QComboBox()
        for box, spec, fallback in ((self.kind, value.get("indicator"), "RSI"),
                                     (self.compare_kind, self.compare_spec(value).get("indicator"), "EMA")):
            self.fill_definitions(box, spec, fallback)
        self.params_host = QWidget()
        self.params_form = QVBoxLayout(self.params_host)
        self.compare_params_host = QWidget()
        self.compare_params_form = QVBoxLayout(self.compare_params_host)
        self.line = QComboBox()
        self.compare_line = QComboBox()
        self.op = QComboBox()
        self.threshold = QLineEdit(str(value.get("threshold", "30")))
        self.compare_enabled = QCheckBox("与另一数值输出比较")
        self.compare_enabled.setChecked("compare" in value)
        self.feedback = QLabel("仅已收盘、连续且新鲜的 K 线参与判断；未知值不会触发。")
        self.feedback.setWordWrap(True)
        for title, widget in (("K 线周期", self.bar), ("指标版本", self.kind), ("参数", self.params_host),
                              ("输出", self.line), ("关系", self.op), ("数值阈值", self.threshold),
                              ("比较指标", self.compare_enabled), ("另一指标版本", self.compare_kind),
                              ("另一指标参数", self.compare_params_host), ("另一数值输出", self.compare_line)):
            form.addRow(title, widget)
        root.addLayout(form)
        root.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.rebuild()
        self.rebuild_compare()
        left = value.get("indicator")
        if left is not None:
            self.input_fields.set_values(runtime_instance(left, catalog).inputs)
        right = self.compare_spec(value)
        if right:
            self.compare_input_fields.set_values(runtime_instance(right.get("indicator"), catalog).inputs)
        select_data(self.line, value.get("line", self.line.currentData()))
        select_data(self.compare_line, right.get("line", self.compare_line.currentData()))
        self.sync_output()
        select_data(self.op, value.get("op", "below"))
        self.kind.currentIndexChanged.connect(self.rebuild)
        self.compare_kind.currentIndexChanged.connect(self.rebuild_compare)
        self.line.currentIndexChanged.connect(self.sync_output)
        self.compare_enabled.toggled.connect(self.sync_compare)
        self.sync_compare()

    @staticmethod
    def compare_spec(value: dict[str, object]) -> dict[str, object]:
        right = value.get("compare")
        return right if isinstance(right, dict) else {}

    def fill_definitions(self, box: QComboBox, spec: object, fallback: str) -> None:
        selected = runtime_instance(spec or {"kind": fallback}, self.catalog)
        versions = {(d.id, d.revision): d for d in self.catalog.list()}
        pinned = self.catalog.resolve(selected.definition, selected.definition_revision)
        versions[pinned.id, pinned.revision] = pinned
        for key, definition in sorted(versions.items(), key=lambda pair: pair[1].name):
            if box is self.compare_kind and not any(output.type != "bool" for output in definition.outputs):
                continue
            box.addItem(f"{definition.name} · v{definition.revision}", f"{key[0]}/{key[1]}")
        select_data(box, f"{pinned.id}/{pinned.revision}")

    def definition(self, box: QComboBox) -> IndicatorDefinition:
        value: object = box.currentData()
        if not isinstance(value, str) or "/" not in value:
            raise ValueError("请选择可用指标版本")
        identity, revision = value.rsplit("/", 1)
        return self.catalog.resolve(identity, int(revision))

    def rebuild_fields(self, box: QComboBox, host: QVBoxLayout, output_box: QComboBox,
                       *, numeric: bool = False) -> InputFields:
        while host.count():
            item = host.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        definition = self.definition(box)
        fields = InputFields(definition)
        host.addWidget(fields)
        output_box.blockSignals(True)
        output_box.clear()
        for output in definition.outputs:
            if not numeric or output.type != "bool":
                output_box.addItem(f"{output.title} ({output.type})", output.id)
        output_box.blockSignals(False)
        return fields

    def rebuild(self) -> None:
        self.input_fields = self.rebuild_fields(self.kind, self.params_form, self.line)
        self.params = self.input_fields.fields
        self.sync_output()

    def rebuild_compare(self) -> None:
        self.compare_input_fields = self.rebuild_fields(self.compare_kind, self.compare_params_form, self.compare_line, numeric=True)
        self.compare_params = self.compare_input_fields.fields

    def sync_output(self) -> None:
        definition = self.definition(self.kind)
        output = next((item for item in definition.outputs if item.id == self.line.currentData()), None)
        boolean = output is not None and output.type == "bool"
        old = self.op.currentData()
        self.op.clear()
        for key, label in (("is_true", "为真"), ("is_false", "为假")) if boolean else (
                ("above", "≥"), ("below", "≤"), ("cross_above", "上穿"), ("cross_below", "下穿")):
            self.op.addItem(label, key)
        select_data(self.op, old)
        self.compare_enabled.setEnabled(not boolean)
        if boolean:
            self.compare_enabled.setChecked(False)
        self.sync_compare()

    def sync_compare(self) -> None:
        boolean = self.op.currentData() in ("is_true", "is_false")
        enabled = not boolean and self.compare_enabled.isChecked()
        for widget in (self.compare_kind, self.compare_params_host, self.compare_line):
            widget.setEnabled(enabled)
        self.threshold.setEnabled(not boolean and not enabled)

    def spec(self, box: QComboBox, fields: InputFields) -> dict[str, object]:
        definition = self.definition(box)
        return {"definition": definition.id, "definition_revision": definition.revision, "inputs": fields.values()}

    def save(self) -> None:
        try:
            candidate: dict[str, object] = {"metric": "indicator", "bar": self.bar.currentData(),
                "indicator": self.spec(self.kind, self.input_fields), "line": self.line.currentData(), "op": self.op.currentData()}
            if self.compare_enabled.isChecked():
                candidate["compare"] = {"indicator": self.spec(self.compare_kind, self.compare_input_fields), "line": self.compare_line.currentData()}
            elif self.op.currentData() not in ("is_true", "is_false"):
                candidate["threshold"] = self.threshold.text().strip()
            validate_condition(candidate, self.catalog)
        except (ValueError, KeyError) as exc:
            self.feedback.setText(str(exc))
            return
        self.condition = candidate
        self.accept()


class RuleDialog(QDialog):
    def __init__(self, service, rule=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("提醒规则")
        self.resize(800, 500)
        rule = rule or {"name": "新提醒", "instrument": service.selected, "mode": "all", "cooldown": 300, "conditions": []}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(rule["name"])
        self.inst = QLineEdit(rule["instrument"])
        self.mode = combo([("all", "全部满足"), ("any", "任一满足"), ("order", "订单事件")])
        select_data(self.mode, rule["mode"])
        self.enabled = QCheckBox("启用")
        self.enabled.setChecked(rule.get("enabled", True))
        self.cooldown = QSpinBox()
        self.cooldown.setRange(0, 86400)
        self.cooldown.setSuffix(" 秒")
        self.cooldown.setValue(rule.get("cooldown", 300))
        self.trigger = combo([("edge", "恢复后再次满足"), ("bar", "每根 K 线最多一次"),
                              ("once", "只触发一次"), ("cooldown", "持续满足并按冷却重复")])
        select_data(self.trigger, rule.get("trigger", "edge"))
        for title, widget in (("名称", self.name), ("合约", self.inst), ("条件组合", self.mode),
                              ("触发方式", self.trigger), ("冷却时间", self.cooldown), ("状态", self.enabled)):
            form.addRow(title, widget)
        layout.addLayout(form)
        self.conditions = QVBoxLayout()
        layout.addLayout(self.conditions)
        self.rows = []
        self.indicator_conditions = [c for c in rule.get("conditions", []) if c.get("metric") == "indicator"]
        for condition in [c for c in rule.get("conditions", []) if c.get("metric") != "indicator"] or ([{"metric": "price", "op": "above", "threshold": "", "window": 900}] if not rule.get("conditions") else []):
            self.add_condition(condition)
        layout.addWidget(button("＋ 添加条件（最多 8 项）", self.add_condition))
        self.indicator_list = QListWidget()
        self.indicator_list.setMaximumHeight(115)
        self.indicator_list.itemDoubleClicked.connect(lambda _: self.edit_indicator())
        layout.addWidget(QLabel("指标条件"))
        layout.addWidget(self.indicator_list)
        indicator_controls = QHBoxLayout()
        for title, callback in (("添加指标条件", self.add_indicator), ("编辑", self.edit_indicator), ("删除", self.delete_indicator)):
            indicator_controls.addWidget(button(title, callback))
        layout.addLayout(indicator_controls)
        self.refresh_indicators()
        states = QHBoxLayout()
        self.state_checks = {}
        for key, label in (("filled", "全部成交"), ("partially_filled", "部分成交"), ("canceled", "已撤销"), ("failed", "失败")):
            check = QCheckBox(label)
            check.setChecked(key in rule.get("states", ["filled", "canceled", "failed"]))
            states.addWidget(check)
            self.state_checks[key] = check
        layout.addLayout(states)
        help_text = QLabel("涨跌幅窗口单位为分钟；放量比较上一根已收盘 K 线与此前 20 根均量。\n启用／恢复后的首个值建立基线；条件先恢复，再次满足时提醒。")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.feedback = QLabel()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox()
        buttons.addButton("保存规则", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rule = None

    def add_condition(self, value=None):
        if len(self.rows) + len(self.indicator_conditions) >= 8:
            return
        value = value or {"metric": "price", "op": "above", "threshold": "", "window": 900}
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        metric = combo(METRICS.items())
        select_data(metric, value["metric"])
        op = combo([("above", "≥"), ("below", "≤")])
        select_data(op, value["op"])
        threshold = QLineEdit(str(value["threshold"]))
        threshold.setPlaceholderText("阈值")
        window = QSpinBox()
        window.setRange(1, 1440)
        window.setSuffix(" 分钟")
        window.setValue(int(value.get("window", 900)) // 60)
        bar = combo([(b, b) for b in BARS])
        select_data(bar, value.get("bar", "15m"))
        for item in (metric, op, threshold, window, bar):
            row.addWidget(item)
        data = (container, metric, op, threshold, window, bar)
        def remove():
            self.rows.remove(data)
            container.deleteLater()
        row.addWidget(button("删除", remove))
        def changed():
            window.setEnabled(metric.currentData() in ("change_pct", "oi_change_pct"))
            bar.setEnabled(metric.currentData() == "volume_ratio")
        metric.currentIndexChanged.connect(changed)
        changed()
        self.rows.append(data)
        self.conditions.addWidget(container)

    def refresh_indicators(self):
        self.indicator_list.clear()
        for condition in self.indicator_conditions:
            right = (spec_title(condition["compare"]["indicator"], self.service.indicator_catalog)
                     if "compare" in condition else condition.get("threshold", ""))
            self.indicator_list.addItem(f"{condition.get('bar', '15m')} · {spec_title(condition['indicator'], self.service.indicator_catalog)} "
                                        f"{condition.get('line', 'value')} {condition['op']} {right}")

    def add_indicator(self):
        if len(self.rows) + len(self.indicator_conditions) >= 8:
            self.feedback.setText("每条规则最多 8 个条件")
            return
        dialog = IndicatorConditionDialog(parent=self, catalog=self.service.indicator_catalog)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.indicator_conditions.append(dialog.condition)
            self.refresh_indicators()

    def edit_indicator(self):
        index = self.indicator_list.currentRow()
        if index < 0:
            return
        try:
            dialog = IndicatorConditionDialog(self.indicator_conditions[index], self, catalog=self.service.indicator_catalog)
        except (ValueError, KeyError) as exc:
            self.feedback.setText(str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.indicator_conditions[index] = dialog.condition
            self.refresh_indicators()
            self.indicator_list.setCurrentRow(index)

    def delete_indicator(self):
        index = self.indicator_list.currentRow()
        if index >= 0:
            self.indicator_conditions.pop(index)
            self.refresh_indicators()

    def _accept(self):
        from coinpilot_ai.trading.alerts import validate_rule
        candidate = {"name": self.name.text(), "instrument": self.inst.text(), "mode": self.mode.currentData(),
                     "enabled": self.enabled.isChecked(), "cooldown": self.cooldown.value(), "trigger": self.trigger.currentData(),
                     "states": [key for key, box in self.state_checks.items() if box.isChecked()],
                     "conditions": [{"metric": metric.currentData(), "op": op.currentData(), "threshold": threshold.text(),
                                     "window": window.value()*60, "bar": bar.currentData()}
                                    for _, metric, op, threshold, window, bar in self.rows] + self.indicator_conditions}
        try:
            self.rule = validate_rule(candidate, catalog=self.service.indicator_catalog)
        except (ValueError, KeyError) as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()
