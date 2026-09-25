"""账户、模型、提示词、规则和桌面行为设置。"""
import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QFormLayout, QInputDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout, QWidget

from coinpilot_ai.review.ai import DEFAULT_PROMPTS, SCENARIOS, service_url
from coinpilot_ai.application.service import DEMO_CHECKS
from coinpilot_ai.ui.common import button, combo, select_data, show_text
from coinpilot_ai.trading.dialogs import RuleDialog
from coinpilot_ai.ui.icons import icon


def secret_edit(placeholder="留空保留已保存的密钥"):
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setPlaceholderText(placeholder)
    return edit


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
            from coinpilot_ai.desktop.settings import SettingsDialog
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
            from coinpilot_ai.updates.ui import UpdateDialog
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
        from coinpilot_ai.charts.dialogs import EmaDialog
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
        profiles = workspace.profiles
        self.profile_choice = combo([(name, name) for name in profiles.names()])
        select_data(self.profile_choice, profiles.active)
        self.profile_choice.currentIndexChanged.connect(lambda: self.activate_profile(profiles))
        layout.addWidget(QLabel("命名工作区"))
        layout.addWidget(self.profile_choice)
        profile_buttons = QHBoxLayout()
        profile_buttons.addWidget(button("保存当前工作区", lambda: profiles.save()))
        profile_buttons.addWidget(button("另存为…", lambda: self.create_profile(profiles)))
        profile_buttons.addWidget(button("删除其他工作区…", lambda: self.delete_profile(profiles)))
        layout.addLayout(profile_buttons)
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

    def activate_profile(self, profiles):
        try:
            profiles.activate(self.profile_choice.currentData())
        except ValueError as exc:
            show_text(self, "工作区切换失败", str(exc))

    def create_profile(self, profiles):
        name, accepted = QInputDialog.getText(self, "保存工作区", "工作区名称")
        if not accepted:
            return
        try:
            name = profiles.save(name)
            self.profile_choice.blockSignals(True)
            self.profile_choice.clear()
            for entry in profiles.names():
                self.profile_choice.addItem(entry, entry)
            select_data(self.profile_choice, profiles.active)
            self.profile_choice.blockSignals(False)
            self.profile_choice.setToolTip(f"已保存 {name}")
        except ValueError as exc:
            show_text(self, "无法保存工作区", str(exc))

    def delete_profile(self, profiles):
        names = [name for name in profiles.names() if name not in ("默认", profiles.active)]
        if not names:
            return
        name, accepted = QInputDialog.getItem(self, "删除工作区", "工作区", names, 0, False)
        if accepted:
            profiles.delete(name)
            index = self.profile_choice.findData(name)
            if index >= 0:
                self.profile_choice.removeItem(index)

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
        self.paper_fee = QLineEdit(str(self.service.settings.get("paper_fee", "0.0005")))
        self.paper_slippage = QLineEdit(str(self.service.settings.get("paper_slippage_bps", "0")))
        form.addRow("本地模拟手续费率（如 0.0005）", self.paper_fee)
        form.addRow("本地模拟滑点（基点）", self.paper_slippage)
        def environment_changed():
            local = self.environment.currentData() == 'paper'
            for edit in (self.api_key, self.api_secret, self.passphrase):
                edit.setEnabled(not local)
        self.environment.currentIndexChanged.connect(environment_changed)
        self.service.updated.connect(lambda kind: select_data(self.environment, self.service.environment) if kind == 'environment' else None)
        environment_changed()
        layout.addLayout(form)
        layout.addWidget(QLabel("本地模拟、OKX 模拟和真实交易记录独立保存。OKX 账户只需读取与交易权限，无需提现权限。"))
        paper_note = QLabel('本地模拟无需 API，初始虚拟资金 100,000 USDT，按实时 OKX 最新价与上方成本参数撮合。'
                            '支持市价、限价、Stop、Stop Limit、Trailing Stop、撤单、平仓和止盈止损，重启保留资金与持仓。'
                            '不模拟盘口排队、资金费、强平及逐仓风险隔离；程序关闭或行情中断期间不撮合。'
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
        from coinpilot_ai.trading.models import number
        fee = number(self.paper_fee.text().strip(), "模拟手续费率")
        slippage = number(self.paper_slippage.text().strip(), "模拟滑点")
        if not 0 <= fee <= 1 or not 0 <= slippage < 10000:
            raise ValueError("模拟手续费率须在 0—1，滑点须低于 10000 基点")
        values = [edit.text().strip() for edit in (self.api_key, self.api_secret, self.passphrase)]
        credentials = dict(zip(("key", "secret", "passphrase"), values)) if any(values) and self.environment.currentData() != 'paper' else None
        self.service.settings["paper_fee"] = str(fee)
        self.service.settings["paper_slippage_bps"] = str(slippage)
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
