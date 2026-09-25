"""账户、模型、提示词、规则和桌面行为设置。"""
import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QSpinBox,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from .ai import DEFAULT_PROMPTS, SCENARIOS, service_url
from .alerts import BARS, METRICS
from .service import DEMO_CHECKS
from .ui_common import button, show_text
from ..icons import icon


def combo(items):
    widget = QComboBox()
    for value, label in items:
        widget.addItem(label, value)
    return widget


def select_data(widget, value):
    index = widget.findData(value)
    widget.setCurrentIndex(max(0, index))


def secret_edit(placeholder="留空保留已保存的密钥"):
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setPlaceholderText(placeholder)
    return edit


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
        for title, widget in (("名称", self.name), ("合约", self.inst), ("条件组合", self.mode), ("冷却时间", self.cooldown), ("状态", self.enabled)):
            form.addRow(title, widget)
        layout.addLayout(form)
        self.conditions = QVBoxLayout()
        layout.addLayout(self.conditions)
        self.rows = []
        for condition in rule.get("conditions", []) or [{"metric": "price", "op": "above", "threshold": "", "window": 900}]:
            self.add_condition(condition)
        layout.addWidget(button("＋ 添加条件（最多 8 项）", self.add_condition))
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
        if len(self.rows) >= 8:
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
            window.setEnabled(metric.currentData() == "change_pct")
            bar.setEnabled(metric.currentData() == "volume_ratio")
        metric.currentIndexChanged.connect(changed)
        changed()
        self.rows.append(data)
        self.conditions.addWidget(container)

    def _accept(self):
        from .alerts import validate_rule
        candidate = {"name": self.name.text(), "instrument": self.inst.text(), "mode": self.mode.currentData(),
                     "enabled": self.enabled.isChecked(), "cooldown": self.cooldown.value(),
                     "states": [key for key, box in self.state_checks.items() if box.isChecked()],
                     "conditions": [{"metric": metric.currentData(), "op": op.currentData(), "threshold": threshold.text(),
                                     "window": window.value()*60, "bar": bar.currentData()}
                                    for _, metric, op, threshold, window, bar in self.rows]}
        try:
            self.rule = validate_rule(candidate)
        except (ValueError, KeyError) as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()


class SettingsPage(QWidget):
    def __init__(self, service, open_legacy=None, parent=None, *, owner=None, updater=None, workspace=None):
        super().__init__(parent)
        self.service = service
        self.provider_id = None
        self.template_id = None
        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(150)
        self.tabs = QStackedWidget()
        self.section_indices = {}
        self.navigation.currentRowChanged.connect(self.tabs.setCurrentIndex)
        row.addWidget(self.navigation)
        row.addWidget(self.tabs, 1)
        root.addLayout(row, 1)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        if owner is not None:
            from ..settings import SettingsDialog
            self.preferences = SettingsDialog(owner, self, embedded=True)
            self.add_section("常规与网络", self.preferences)
        self._chart()
        if workspace is not None:
            self._workspace(workspace)
        self._account()
        self._providers()
        self._prompts()
        self._rules()
        self._general()
        if updater is not None:
            from ..update_ui import UpdateDialog
            self.update_panel = UpdateDialog(updater, self, embedded=True)
            updater.dialog = self.update_panel
            self.update_panel.destroyed.connect(updater.dialog_destroyed)
            self.add_section("软件更新", self.update_panel)
        self.navigation.setCurrentRow(0)
        self.reload()

    def add_section(self, title, content):
        self.section_indices[title] = self.tabs.addWidget(content)
        item = QListWidgetItem(icon("settings"), title)
        self.navigation.addItem(item)

    def select_section(self, title):
        self.navigation.setCurrentRow(self.section_indices.get(title, 0))

    def _chart(self):
        from .chart_dialogs import EmaDialog
        layout = self.page("图表")
        self.magnet = QCheckBox("K 线高低点磁吸（立即生效，按 Alt 临时关闭）")
        self.magnet.setChecked(self.service.chart_book.magnet_enabled)
        self.magnet.toggled.connect(self.service.chart_book.save_magnet)
        layout.addWidget(self.magnet)
        self.ema_editor = EmaDialog(self.service.chart_book, self, embedded=True)
        layout.addWidget(self.ema_editor, 1)
        self.service.chart_book.changed.connect(self.sync_chart)

    def sync_chart(self, kind, _key):
        if kind == "magnet":
            self.magnet.blockSignals(True)
            self.magnet.setChecked(self.service.chart_book.magnet_enabled)
            self.magnet.blockSignals(False)

    def _workspace(self, workspace):
        layout = self.page("工作区布局")
        layout.addWidget(QLabel("面板开关与布局调整立即生效，并自动保存。"))
        for key, action in workspace.actions.items():
            check = QCheckBox(action.text())
            check.setChecked(action.isChecked())
            check.toggled.connect(lambda checked, k=key: workspace.set_visible(k, checked))
            action.toggled.connect(check.setChecked)
            layout.addWidget(check)
        lock = QCheckBox("锁定面板停靠布局")
        lock.setChecked(workspace.locked)
        lock.toggled.connect(workspace.set_locked)
        workspace.lock_action.toggled.connect(lock.setChecked)
        layout.addWidget(lock)
        layout.addWidget(button("恢复默认布局", workspace.reset))
        layout.addStretch()

    def page(self, title):
        content = QWidget()
        content.setObjectName("workbenchRoot")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 16, 18, 16)
        heading = QLabel(title)
        heading.setObjectName("title")
        layout.addWidget(heading)
        descriptions = {"OKX 账户": "连接你的账户，分别管理模拟与真实交易。", "AI 服务": "配置模型连接，并为每个分析场景选择服务。",
                        "提示词模板": "定义分析问题与报告结构，每次保存保留独立版本。", "提醒规则": "在重要变化发生时提醒你。", "桌面与通知": "调整桌面行为与提醒方式。"}
        description = QLabel(descriptions.get(title, ""))
        description.setObjectName("muted")
        layout.addWidget(description)
        layout.addSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        name = "wallet" if "账户" in title else "sparkles" if "AI" in title else "notebook-pen" if "提示词" in title else "bell" if "规则" in title else "settings"
        self.add_section(title, scroll)
        return layout

    def run(self, action):
        try:
            action()
        except (ValueError, OSError, KeyError) as exc:
            self.feedback.setText(str(exc))

    def _account(self):
        layout = self.page("OKX 账户")
        form = QFormLayout()
        self.environment = combo([("paper", "本地模拟（无需 API）"), ("demo", "OKX 模拟交易"), ("live", "真实交易")])
        select_data(self.environment, self.service.environment)
        self.api_key, self.api_secret, self.passphrase = secret_edit(), secret_edit(), secret_edit()
        for title, widget in (("环境", self.environment), ("API Key", self.api_key), ("Secret", self.api_secret), ("Passphrase", self.passphrase)):
            form.addRow(title, widget)
        def environment_changed():
            local = self.environment.currentData() == 'paper'
            for edit in (self.api_key, self.api_secret, self.passphrase):
                edit.setEnabled(not local)
        self.environment.currentIndexChanged.connect(environment_changed)
        self.service.updated.connect(lambda kind: select_data(self.environment, self.service.environment) if kind == 'environment' else None)
        environment_changed()
        layout.addLayout(form)
        layout.addWidget(QLabel("本地模拟、OKX 模拟和真实交易记录独立保存。OKX 账户只需读取与交易权限，无需提现权限。"))
        paper_note = QLabel('本地模拟无需 API，初始虚拟资金 100,000 USDT，按实时 OKX 最新价整笔成交，手续费 0.05%。'
                            '支持市价／限价、撤单、平仓、止盈止损和杠杆，重启保留资金与持仓。'
                            '不模拟盘口排队、滑点、资金费、强平及逐仓风险隔离；程序关闭或行情中断期间不撮合。'
                            '本地模拟记录不用于解锁真实交易。')
        paper_note.setWordWrap(True)
        layout.addWidget(paper_note)
        actions = QHBoxLayout()
        actions.addWidget(button("保存／切换并连接账户", lambda: self.run(self.save_account), True))
        actions.addWidget(button("刷新账户与验收状态", self.service.refresh_account))
        actions.addStretch()
        layout.addLayout(actions)
        self.account_status = QLabel()
        self.account_status.setWordWrap(True)
        layout.addWidget(self.account_status)
        self.check_status = QLabel()
        self.check_status.setWordWrap(True)
        layout.addWidget(self.check_status)
        layout.addWidget(QLabel("模拟验收按交易所返回的实际结果记录；全部通过后才能在真实环境提交交易。"))
        layout.addStretch()

    def save_account(self):
        values = [edit.text().strip() for edit in (self.api_key, self.api_secret, self.passphrase)]
        credentials = dict(zip(("key", "secret", "passphrase"), values)) if any(values) and self.environment.currentData() != 'paper' else None
        self.service.change_account(self.environment.currentData(), credentials)
        for edit in (self.api_key, self.api_secret, self.passphrase):
            edit.clear()
        self.feedback.setText("账户配置已应用；等待同步结果")

    def _providers(self):
        layout = self.page("AI 服务")
        self.providers = combo([])
        self.providers.currentIndexChanged.connect(self.load_provider)
        layout.addWidget(self.providers)
        form = QFormLayout()
        self.provider_name = QLineEdit()
        self.protocol = combo([("responses", "OpenAI Responses"), ("messages", "Anthropic Messages"), ("chat", "OpenAI Chat Completions")])
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://服务地址/v1")
        self.model = QLineEdit()
        self.model.setPlaceholderText("填写服务实际支持的模型名称")
        self.ai_key = secret_edit()
        for title, widget in (("名称", self.provider_name), ("协议", self.protocol), ("接口地址", self.base_url), ("模型", self.model), ("API Key", self.ai_key)):
            form.addRow(title, widget)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addWidget(button("新建服务", self.new_provider))
        buttons.addWidget(button("保存服务", lambda: self.run(self.save_provider), True))
        buttons.addWidget(button("测试已保存连接", lambda: self.run(self.test_provider)))
        buttons.addStretch()
        layout.addLayout(buttons)
        self.scene_services, self.scene_templates = {}, {}
        mappings = QFormLayout()
        for key, label in SCENARIOS.items():
            row = QHBoxLayout()
            provider, template = combo([]), combo([])
            row.addWidget(provider)
            row.addWidget(template)
            mappings.addRow(label, row)
            self.scene_services[key], self.scene_templates[key] = provider, template
        layout.addLayout(mappings)
        layout.addWidget(button("保存各场景的服务和默认模板", self.save_mappings), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()

    def new_provider(self):
        self.provider_id = None
        for edit in (self.provider_name, self.base_url, self.model, self.ai_key):
            edit.clear()

    def load_provider(self):
        if self.service.closed:
            return
        key = self.providers.currentData()
        row = self.service.store.get("ai_service", key) if key else None
        if not row:
            self.new_provider()
            return
        self.provider_id = key
        self.provider_name.setText(row["name"])
        self.base_url.setText(row["base_url"])
        self.model.setText(row["model"])
        select_data(self.protocol, row["protocol"])
        self.ai_key.clear()

    def save_provider(self):
        service_url(self.base_url.text(), self.protocol.currentData())
        if not self.provider_name.text().strip() or not self.model.text().strip():
            raise ValueError("请填写服务名称与模型")
        key = self.provider_id or uuid.uuid4().hex
        if self.ai_key.text().strip():
            self.service.vault.write("ai/" + key, {"key": self.ai_key.text().strip()})
        row = {"id": key, "name": self.provider_name.text().strip(), "protocol": self.protocol.currentData(),
               "base_url": self.base_url.text().strip().rstrip("/"), "model": self.model.text().strip()}
        self.service.store.put("ai_service", key, row)
        self.provider_id = key
        self.reload_providers()
        select_data(self.providers, key)
        self.feedback.setText("服务已保存；密钥保存在 Windows 凭据管理器")

    def test_provider(self):
        config = self.service.store.get("ai_service", self.provider_id)
        if not config:
            raise ValueError("请先保存服务")
        self.feedback.setText("正在测试连接…")
        self.service.ai.ask(config, "请仅回复：连接成功", lambda answer, error: self.feedback.setText(str(error) if error else "连接成功：" + answer[:200]))

    def save_mappings(self):
        self.service.settings["scenes"] = {key: {"service": self.scene_services[key].currentData(), "template": self.scene_templates[key].currentData()} for key in SCENARIOS}
        self.service.save_settings()
        self.feedback.setText("各场景配置已保存")

    def _prompts(self):
        layout = self.page("提示词模板")
        self.scenario = combo(SCENARIOS.items())
        self.templates = combo([])
        self.scenario.currentIndexChanged.connect(self.reload_templates)
        self.templates.currentIndexChanged.connect(self.load_template)
        row = QHBoxLayout()
        row.addWidget(self.scenario)
        row.addWidget(self.templates)
        layout.addLayout(row)
        self.template_name = QLineEdit()
        self.template_body = QTextEdit()
        self.template_body.setMinimumHeight(240)
        layout.addWidget(self.template_name)
        layout.addWidget(self.template_body)
        variables = combo([(v, "{{" + v + "}}") for v in ("context", "market", "positions", "trades", "events", "question")])
        row = QHBoxLayout()
        row.addWidget(variables)
        row.addWidget(button("插入变量", lambda: self.template_body.insertPlainText("{{" + variables.currentData() + "}}")))
        row.addWidget(button("恢复默认内容", lambda: self.template_body.setPlainText(DEFAULT_PROMPTS[self.scenario.currentData()])))
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("新建", self.new_template))
        row.addWidget(button("复制当前模板", self.copy_template))
        row.addWidget(button("保存新版本", lambda: self.run(self.save_template), True))
        layout.addLayout(row)

    def new_template(self):
        self.template_id = None
        self.template_name.setText("新模板")
        self.template_body.setPlainText("{{context}}")

    def copy_template(self):
        self.template_id = None
        self.template_name.setText(self.template_name.text() + " 副本")

    def load_template(self):
        if self.service.closed:
            return
        key = self.templates.currentData()
        row = self.service.store.get("template", key) if key else None
        if row:
            self.template_id = key
            self.template_name.setText(row["name"])
            self.template_body.setPlainText(row["body"])

    def save_template(self):
        key = self.service.prompts.save(self.scenario.currentData(), self.template_name.text(), self.template_body.toPlainText(), self.template_id)
        self.reload_templates()
        select_data(self.templates, key)
        self.reload_mappings()
        self.feedback.setText("提示词新版本已保存；历史报告仍保留原版本")

    def _rules(self):
        layout = self.page("提醒规则")
        self.rule_list = QListWidget()
        layout.addWidget(self.rule_list)
        row = QHBoxLayout()
        row.addWidget(button("新增规则", lambda: self.edit_rule(False), True))
        row.addWidget(button("编辑所选", lambda: self.edit_rule(True)))
        row.addWidget(button("删除所选", self.delete_rule))
        layout.addLayout(row)

    def edit_rule(self, existing):
        item = self.rule_list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if existing and item else None
        if existing and not key:
            return
        rule = self.service.store.get("rule", key, scope=self.service.environment) if key else None
        dialog = RuleDialog(self.service, rule, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.service.save_rule(dialog.rule, key)
            self.reload_rules()

    def delete_rule(self):
        item = self.rule_list.currentItem()
        if item:
            self.service.store.delete("rule", item.data(Qt.ItemDataRole.UserRole), self.service.environment)
            self.service.refresh_market()
            self.reload_rules()

    def _general(self):
        layout = self.page("桌面与通知")
        self.pause = QCheckBox("暂停弹出通知（事件继续记录）")
        self.sound = QCheckBox("持仓与订单事件播放提示音")
        self.auto_ai = QCheckBox("规则触发后自动请求 AI 解读")
        for widget, key in ((self.pause, "notifications_paused"), (self.sound, "sound"), (self.auto_ai, "auto_explain")):
            widget.setChecked(self.service.settings.get(key, False))
            widget.toggled.connect(lambda value, name=key: self.save_toggle(name, value))
            layout.addWidget(widget)
        layout.addStretch()

    def refresh_status(self):
        # 导航不重新加载表单，保留尚未保存的服务、密钥和提示词草稿。
        self.update_status()
        for control, key in ((self.pause, "notifications_paused"), (self.sound, "sound"), (self.auto_ai, "auto_explain")):
            control.blockSignals(True)
            control.setChecked(self.service.settings.get(key, False))
            control.blockSignals(False)

    def save_toggle(self, key, value):
        self.service.settings[key] = value
        self.service.save_settings()

    def reload_providers(self):
        old = self.provider_id
        self.providers.blockSignals(True)
        self.providers.clear()
        for key, row in self.service.store.list("ai_service"):
            self.providers.addItem(row["name"], key)
        select_data(self.providers, old)
        self.providers.blockSignals(False)
        self.load_provider()
        self.reload_mappings()

    def reload_mappings(self):
        for scene in SCENARIOS:
            provider, template = self.scene_services[scene], self.scene_templates[scene]
            provider.clear()
            provider.addItem("尚未配置", None)
            for key, row in self.service.store.list("ai_service"):
                provider.addItem(row["name"], key)
            template.clear()
            for key, row in self.service.prompts.templates(scene):
                template.addItem(row["name"] + f" · v{row['version']}", key)
            mapping = self.service.settings.get("scenes", {}).get(scene, {})
            select_data(provider, mapping.get("service"))
            select_data(template, mapping.get("template"))

    def reload_templates(self):
        self.templates.blockSignals(True)
        self.templates.clear()
        for key, row in self.service.prompts.templates(self.scenario.currentData()):
            self.templates.addItem(row["name"] + f" · v{row['version']}", key)
        self.templates.blockSignals(False)
        self.load_template()

    def reload_rules(self):
        self.rule_list.clear()
        for key, row in self.service.rules():
            item = QListWidgetItem(("● " if row.get("enabled", True) else "○ ") + row["name"] + "  ·  " + row["instrument"])
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.rule_list.addItem(item)

    def update_status(self):
        self.account_status.setText(('本地虚拟账户已就绪 · 可用 ' + self.service.balance.get('availEq', '—') + ' USDT') if self.service.environment == 'paper' else self.service.account_error or "账户已连接 · " + self.service.account.get("posMode", ""))
        lines = [("✓ " if self.service.store.get("demo_check", key) else "○ ") + label for key, label in DEMO_CHECKS.items()]
        self.check_status.setText("OKX 模拟盘验收（仅用于解锁真实交易）：\n" + "    ".join(lines))

    def reload(self):
        self.reload_providers()
        self.reload_templates()
        self.reload_rules()
        self.update_status()
