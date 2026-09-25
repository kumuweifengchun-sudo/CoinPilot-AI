"""提醒规则与指标条件编辑器，供工作台设置和策略研究共用。"""
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QSpinBox, QVBoxLayout, QWidget

from coinpilot_ai.market.indicators import DEFAULTS, KINDS, validate_spec
from coinpilot_ai.ui.common import button, combo, select_data
from .alerts import BARS, METRICS


class IndicatorConditionDialog(QDialog):
    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("指标提醒条件")
        self.resize(420, 360)
        value = value or {}
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.bar = combo([(bar, bar) for bar in BARS])
        select_data(self.bar, value.get("bar", "15m"))
        self.kind = combo([(name, name) for name in sorted(KINDS)])
        select_data(self.kind, value.get("indicator", {}).get("kind", "RSI"))
        self.kind.currentIndexChanged.connect(self.rebuild)
        self.params_host = QWidget()
        self.params_form = QFormLayout(self.params_host)
        self.line = QComboBox()
        self.op = combo([("above", "≥"), ("below", "≤"), ("cross_above", "上穿"), ("cross_below", "下穿")])
        select_data(self.op, value.get("op", "below"))
        self.threshold = QLineEdit(str(value.get("threshold", "30")))
        self.compare_enabled = QCheckBox("与另一指标比较")
        self.compare_enabled.setChecked("compare" in value)
        self.compare_kind = combo([(name, name) for name in sorted(KINDS)])
        select_data(self.compare_kind, value.get("compare", {}).get("indicator", {}).get("kind", "EMA"))
        self.compare_line = QComboBox()
        self.compare_kind.currentIndexChanged.connect(self.rebuild_compare)
        self.compare_params_host = QWidget()
        self.compare_params_form = QFormLayout(self.compare_params_host)
        self.compare_enabled.toggled.connect(self.sync_compare)
        for title, widget in (("K 线周期", self.bar), ("指标", self.kind), ("参数", self.params_host),
                              ("数值线", self.line), ("关系", self.op), ("数值阈值", self.threshold),
                              ("比较指标", self.compare_enabled), ("另一指标", self.compare_kind),
                              ("另一指标参数", self.compare_params_host), ("另一数值线", self.compare_line)):
            form.addRow(title, widget)
        root.addLayout(form)
        self.feedback = QLabel("只有已收盘、连续且新鲜的 K 线参与判断。")
        root.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.rebuild()
        self.rebuild_compare()
        self.set_params(self.params, value.get("indicator", {}).get("params", {}))
        self.set_params(self.compare_params, value.get("compare", {}).get("indicator", {}).get("params", {}))
        select_data(self.line, value.get("line", self.line.currentData()))
        select_data(self.compare_line, value.get("compare", {}).get("line", self.compare_line.currentData()))
        self.sync_compare()
        self.condition = None

    @staticmethod
    def set_params(fields, values):
        for key, field in fields.items():
            if key in values:
                field.setValue(values[key])

    @staticmethod
    def clear_form(form):
        while form.count():
            item = form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def rebuild_fields(self, kind, form, line_box):
        self.clear_form(form)
        fields = {}
        for key, value in DEFAULTS[kind].items():
            field = QDoubleSpinBox() if key in ("deviations", "multiplier") else QSpinBox()
            field.setRange(.01 if isinstance(field, QDoubleSpinBox) else 1,
                           100 if isinstance(field, QDoubleSpinBox) else 1000)
            field.setValue(value)
            fields[key] = field
            form.addRow(key, field)
        line_box.clear()
        names = {"MACD": ("macd", "signal", "histogram"), "BOLL": ("middle", "upper", "lower"),
                 "STOCHASTIC": ("k", "d")}.get(kind, ("value",))
        for name in names:
            line_box.addItem(name, name)
        return fields

    def rebuild(self):
        self.params = self.rebuild_fields(self.kind.currentData(), self.params_form, self.line)

    def rebuild_compare(self):
        self.compare_params = self.rebuild_fields(self.compare_kind.currentData(), self.compare_params_form, self.compare_line)

    def sync_compare(self):
        enabled = self.compare_enabled.isChecked()
        for widget in (self.compare_kind, self.compare_params_host, self.compare_line):
            widget.setEnabled(enabled)
        self.threshold.setEnabled(not enabled)

    def save(self):
        candidate = {"metric": "indicator", "bar": self.bar.currentData(),
                     "indicator": validate_spec({"kind": self.kind.currentData(),
                                                 "params": {k: w.value() for k, w in self.params.items()}}),
                     "line": self.line.currentData(), "op": self.op.currentData()}
        if self.compare_enabled.isChecked():
            candidate["compare"] = {"indicator": validate_spec({"kind": self.compare_kind.currentData(),
                "params": {k: w.value() for k, w in self.compare_params.items()}}), "line": self.compare_line.currentData()}
        else:
            candidate["threshold"] = self.threshold.text().strip()
        try:
            from coinpilot_ai.trading.models import number
            if "threshold" in candidate:
                number(candidate["threshold"])
        except ValueError as exc:
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
            right = condition.get("compare", {}).get("indicator", {}).get("kind", condition.get("threshold", ""))
            self.indicator_list.addItem(f"{condition.get('bar', '15m')} · {condition['indicator']['kind']} "
                                        f"{condition.get('line', 'value')} {condition['op']} {right}")

    def add_indicator(self):
        if len(self.rows) + len(self.indicator_conditions) >= 8:
            self.feedback.setText("每条规则最多 8 个条件")
            return
        dialog = IndicatorConditionDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.indicator_conditions.append(dialog.condition)
            self.refresh_indicators()

    def edit_indicator(self):
        index = self.indicator_list.currentRow()
        if index < 0:
            return
        dialog = IndicatorConditionDialog(self.indicator_conditions[index], self)
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
            self.rule = validate_rule(candidate)
        except (ValueError, KeyError) as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()
