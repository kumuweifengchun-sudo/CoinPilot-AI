"""可收起的场景 AI 面板；草稿与实际下单分离。"""
from copy import deepcopy

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QTextBrowser, QTextEdit, QVBoxLayout, QWidget

from .ai import parse_draft, render_prompt
from coinpilot_ai.trading.models import OrderDraft
from coinpilot_ai.ui.common import button, show_text


class AiPanel(QGroupBox):
    draft_ready = pyqtSignal(object)

    def __init__(self, service, scene, context_provider, parent=None):
        super().__init__({"event": "AI · 事件解读", "order": "AI · 交易计划", "review": "AI · 自定义复盘"}[scene], parent)
        self.service, self.scene, self.context_provider = service, scene, context_provider
        self.task = None
        self.last_record = None
        self.last_request = None
        self.setCheckable(True)
        root = QVBoxLayout(self)
        self.body = QWidget()
        layout = QVBoxLayout(self.body)
        self.templates = QComboBox()
        self.templates.currentIndexChanged.connect(self.load_template)
        layout.addWidget(self.templates)
        self.prompt = QTextEdit()
        self.prompt.setMaximumHeight(110)
        layout.addWidget(self.prompt)
        self.question = QLineEdit()
        self.question.setPlaceholderText("补充问题或要求（可选）")
        layout.addWidget(self.question)
        row = QHBoxLayout()
        self.ask_button = button("生成分析" if scene != "review" else "生成复盘", self.ask, True)
        row.addWidget(self.ask_button)
        row.addWidget(button("预览发送内容", self.preview))
        row.addWidget(button("重试上次请求", self.retry))
        self.cancel_button = button("取消请求", lambda: self.service.cancel_ai(self.task))
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        if scene == "order":
            self.draft_button = button("请求订单草稿", lambda: self.ask(draft=True))
            row.addWidget(self.draft_button)
        layout.addLayout(row)
        self.status = QLabel("选择模板后可临时修改提示词；请求不会覆盖已保存模板。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.answer = QTextBrowser()
        self.answer.setOpenExternalLinks(False)
        self.answer.setMinimumHeight(140)
        layout.addWidget(self.answer, 1)
        if scene == "order":
            self.load_draft_button = button("载入订单草稿到下单面板", self.load_draft)
            self.load_draft_button.setEnabled(False)
            layout.addWidget(self.load_draft_button)
        if scene == "review":
            layout.addWidget(button("针对当前报告追问", self.followup))
        root.addWidget(self.body)
        self.toggled.connect(self.set_expanded)
        self.setChecked(scene == "review")
        self.set_expanded(scene == "review")
        service.ai_finished.connect(self.finished)
        self.reload_templates()

    def set_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.setMaximumHeight(16777215 if expanded else 36)
        self.updateGeometry()

    def reload_templates(self):
        old = self.templates.currentData()
        edited = hasattr(self, "loaded_body") and self.prompt.toPlainText() != self.loaded_body
        temporary = self.prompt.toPlainText()
        self.templates.blockSignals(True)
        self.templates.clear()
        for key, row in self.service.prompts.templates(self.scene):
            self.templates.addItem(row["name"] + f" · v{row['version']}", key)
        selected = old or self.service.scene_template(self.scene)["id"]
        self.templates.setCurrentIndex(max(0, self.templates.findData(selected)))
        self.templates.blockSignals(False)
        self.load_template()
        if edited and self.templates.currentData() == old:
            self.prompt.setPlainText(temporary)

    def load_template(self):
        if self.service.closed:
            return
        row = self.service.store.get("template", self.templates.currentData())
        if row:
            self.prompt.setPlainText(row["body"])
            self.loaded_body = row["body"]

    def request_values(self):
        context = self.context_provider()
        context["question"] = self.question.text().strip()
        template = deepcopy(self.service.store.get("template", self.templates.currentData()) or self.service.scene_template(self.scene))
        template["edited_for_request"] = template["body"] != self.prompt.toPlainText()
        template["body"] = self.prompt.toPlainText()
        if not template["body"].strip():
            raise ValueError("请填写提示词")
        return context, template

    def preview(self):
        try:
            context, template = self.request_values()
            show_text(self, "发送给 AI 的提示词与事实资料", render_prompt(template["body"], context))
        except ValueError as exc:
            self.status.setText(str(exc))

    def ask(self, draft=False):
        if self.task:
            return
        try:
            context, template = self.request_values()
            self.start_request(context, template, draft)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def start_request(self, context, template, draft=False):
        self.last_request = (deepcopy(context), deepcopy(template), draft)
        self.task = self.service.ask_ai(self.scene, context, template, draft)
        self.status.setText("正在请求 AI…")
        self.ask_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        if self.scene == "order":
            self.draft_button.setEnabled(False)
            self.load_draft_button.setEnabled(False)

    def retry(self):
        if not self.task and self.last_request:
            try:
                self.start_request(*self.last_request)
            except ValueError as exc:
                self.status.setText(str(exc))

    def finished(self, task, record):
        if task != self.task:
            return
        self.task = None
        self.ask_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self.scene == "order":
            self.draft_button.setEnabled(True)
        if record["scope"] != self.service.scope:
            self.status.setText("账户环境已切换；该请求结果保存在原环境。")
            return
        self.show_record(record)

    def show_record(self, record):
        self.last_record = record
        self.answer.setMarkdown(record.get("answer", ""))
        self.status.setText(record.get("error") or f"已保存 · {record['service']['name']} / {record['service']['model']} · 提示词 v{record['template']['version']}")
        if self.scene == "order":
            self.load_draft_button.setEnabled(record.get("draft_requested", False) and record["status"] == "complete")

    def load_draft(self):
        try:
            if not self.last_record or not self.last_record.get("draft_requested"):
                raise ValueError("请先主动请求订单草稿")
            draft = OrderDraft.from_ai(parse_draft(self.last_record["answer"]))
            self.draft_ready.emit(draft)
        except ValueError as exc:
            self.status.setText(str(exc))

    def followup(self):
        if self.task:
            return
        if not self.last_record or self.last_record.get("status") != "complete":
            self.status.setText("请先生成或选中一份完整报告")
            return
        if not self.question.text().strip():
            self.status.setText("请在补充问题中填写追问内容")
            return
        try:
            context = deepcopy(self.last_record["context"])
            context.update(previous_report=self.last_record["answer"], parent_report=self.last_record.get("id"), question=self.question.text().strip())
            template = deepcopy(self.last_record["template"])
            template["body"] = "请基于原报告和事实资料回答用户的追问。\n{{question}}\n{{context}}"
            template["edited_for_request"] = True
            self.start_request(context, template)
        except ValueError as exc:
            self.status.setText(str(exc))
