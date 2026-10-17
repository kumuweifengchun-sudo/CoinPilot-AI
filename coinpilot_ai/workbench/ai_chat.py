from collections.abc import Callable
import sqlite3
from typing import override

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QHideEvent, QKeyEvent, QShowEvent
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget

from coinpilot_ai.ai.context import WorkspaceSnapshot
from coinpilot_ai.ai.engine import AiChatService
from coinpilot_ai.ai.events import AiEvent
from coinpilot_ai.ai.models import Item, PinKind, validate_payload
from coinpilot_ai.ai.presets import PRESETS, Preset
from coinpilot_ai.ai.providers.base import ProviderConfig
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.trading.models import OrderDraft
from coinpilot_ai.charts.panel import ChartPanel
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.indicators.editor import IndicatorEditor
from coinpilot_ai.indicators.models import IndicatorInstance
from coinpilot_ai.indicators.validation import instance_record
from coinpilot_ai.ui.qt import require
from coinpilot_ai.ui.theme import SPACING_SM, SPACING_XS
from .ai_cells import AiIndicatorDraftCard, AiMessageCell, AiOrderDraftCard, AiToolCard


class ChatInput(QTextEdit):
    send_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    @override
    def keyPressEvent(self, e: QKeyEvent | None) -> None:
        if e is not None:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.send_requested.emit()
                e.accept()
                return
            if e.key() == Qt.Key.Key_Escape:
                self.stop_requested.emit()
                e.accept()
                return
        super().keyPressEvent(e)


class AiChatPanel(QWidget):
    draft_ready = pyqtSignal(object)

    def __init__(self, business: CockpitService, context_provider: Callable[[], WorkspaceSnapshot],
                 parent: QWidget | None = None, *, chart_provider: Callable[[], ChartPanel] | None = None) -> None:
        super().__init__(parent)
        self.business, self.chat, self.context_provider = business, business.ai_chat, context_provider
        self.chart_provider = chart_provider
        self._cells: dict[str, QWidget] = {}
        self._tool_card_ids: dict[str, str] = {}
        self._pending_text: dict[str, str] = {}
        self._shown: tuple[str, str] | None = None
        self._drafts: dict[tuple[str, str], str] = {}
        self._oldest_sequence: int | None = None
        self._presets: list[Preset] = list(PRESETS)
        self.setMinimumSize(280, 200)
        root = QVBoxLayout(self)
        # 外层工作区容器提供统一内边距，避免聊天面板重复叠加留白。
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACING_SM)
        header = QHBoxLayout()
        header.setSpacing(SPACING_XS)
        title = QLabel("CoinPilot AI")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        self.new_button = QPushButton("＋")
        self.new_button.setToolTip("新对话")
        self.new_button.setFixedWidth(30)
        self.new_button.clicked.connect(self.new_chat)
        header.addWidget(self.new_button)
        self.regenerate_button = QPushButton("重新生成")
        self.regenerate_button.setFixedWidth(80)
        self.regenerate_button.clicked.connect(self.regenerate)
        header.addWidget(self.regenerate_button)
        root.addLayout(header)
        self.context_source = QComboBox()
        self.context_source.setToolTip("对话按交易账户或手动账本隔离；切换会停止当前请求")
        self.context_source.currentIndexChanged.connect(
            lambda: self.business.select_review_book(str(self.context_source.currentData() or "")))
        root.addWidget(self.context_source)
        self.providers = QComboBox()
        self.providers.currentIndexChanged.connect(self.choose_provider)
        root.addWidget(self.providers)
        self.history = QComboBox()
        self.history.currentIndexChanged.connect(self.choose_history)
        root.addWidget(self.history)
        row = QHBoxLayout()
        row.setSpacing(SPACING_XS)
        self.preset = QComboBox()
        for preset in PRESETS:
            self.preset.addItem(preset.title, preset.id)
        row.addWidget(self.preset)
        chart_pin = QPushButton("固定图表")
        chart_pin.clicked.connect(self.pin_chart)
        row.addWidget(chart_pin)
        position_pin = QPushButton("当前仓位")
        position_pin.clicked.connect(lambda: self._run(lambda: self.pin("position", "current", "当前仓位")))
        row.addWidget(position_pin)
        root.addLayout(row)
        self.pin_host = QWidget()
        self.pin_layout = QHBoxLayout(self.pin_host)
        self.pin_layout.setContentsMargins(0, 0, 0, 0)
        self.pin_layout.setSpacing(SPACING_XS)
        pins = QScrollArea()
        pins.setWidgetResizable(True)
        pins.setFixedHeight(38)
        pins.setWidget(self.pin_host)
        root.addWidget(pins)
        self.message_scroll = QScrollArea()
        self.message_scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.messages = QVBoxLayout(self.content)
        self.messages.setContentsMargins(0, 0, 0, 0)
        self.messages.setSpacing(SPACING_SM)
        self.messages.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.message_scroll.setWidget(self.content)
        root.addWidget(self.message_scroll, 1)
        self.more = QPushButton("加载更早的消息")
        self.more.clicked.connect(lambda: self._run(self.load_more))
        root.addWidget(self.more)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        self.input = ChatInput()
        self.input.setPlaceholderText("问 CoinPilot AI…\nEnter 发送 · Shift+Enter 换行 · Esc 停止")
        self.input.setMaximumHeight(90)
        self.input.send_requested.connect(self.send)
        self.input.stop_requested.connect(self.stop)
        root.addWidget(self.input)
        self.send_button = QPushButton("发送 ↑")
        self.send_button.clicked.connect(self.send_or_stop)
        root.addWidget(self.send_button)
        self.render_timer = QTimer(self)
        self.render_timer.setInterval(75)
        self.render_timer.timeout.connect(self.flush_text)
        if self.chat is not None:
            self.chat.event_received.connect(self.on_event)
            self.chat.changed.connect(self.reload)
        business.updated.connect(self.business_changed)
        self.reload()

    def business_changed(self, kind: str) -> None:
        if kind in ("environment", "ai_settings", "manual"):
            self.reload()

    def _run(self, operation: Callable[[], object]) -> None:
        try:
            operation()
            self.feedback.clear()
        except (ValueError, LookupError, OSError) as error:
            self.feedback.setText(str(error))
        except sqlite3.Error:
            if self.chat is not None:
                self.chat.handle_storage_error()

    def reload(self) -> None:
        try:
            self._reload()
        except sqlite3.Error:
            if self.chat is not None:
                self.chat.handle_storage_error()

    def _reload(self) -> None:
        chat = self.chat
        if self.business.closed or chat is not None and chat.closed:
            return
        if chat is None:
            self.feedback.setText(self.business.ai_chat_error or "AI 服务不可用")
            self.send_button.setEnabled(False)
            return
        if chat.storage_error:
            self.feedback.setText(chat.storage_error)
            for control in (self.send_button, self.new_button, self.regenerate_button, self.providers, self.history):
                control.setEnabled(False)
            self.render_timer.stop()
            return
        key = (chat.scope, chat.conversation.id)
        self.context_source.blockSignals(True)
        self.context_source.clear()
        self.context_source.addItem("对话范围 · 当前交易账户", "")
        for book in self.business.manual_ledger.books():
            self.context_source.addItem(f"手动账本 · {book['name']} / {book['account']}", book["id"])
        book_id = chat.scope.removeprefix("manual:") if chat.scope.startswith("manual:") else ""
        self.context_source.setCurrentIndex(max(0, self.context_source.findData(book_id)))
        self.context_source.blockSignals(False)
        if self._shown is not None and self._shown != key:
            self._drafts[self._shown] = self.input.toPlainText()
            self.input.setPlainText(self._drafts.get(key, ""))
        self._shown = key
        selected_preset = self.preset.currentData()
        self._presets = list(PRESETS)
        for template_id, record in self.business.store.list("template", limit=50):
            try:
                row = validate_payload(record)
            except ValueError:
                continue
            name, body = row.get("name"), row.get("body")
            if not isinstance(name, str) or not isinstance(body, str):
                continue
            # 原模板变量改为按需读取提示，不展开全量账户／历史上下文。
            for variable in ("context", "market", "positions", "trades", "events", "question"):
                body = body.replace("{{" + variable + "}}", "[使用当前问题和工作区资料；缺少事实时按权限调用工具]")
            self._presets.append(Preset("template/" + template_id, "模板 · " + name, body))
        self.preset.clear()
        for preset in self._presets:
            self.preset.addItem(preset.title, preset.id)
        self.preset.setCurrentIndex(max(0, self.preset.findData(selected_preset)))
        self.preset.setEnabled(not chat.running)
        provider_ready = self._reload_provider_selector(chat)
        self.history.blockSignals(True)
        self.history.clear()
        for conversation in chat.store.list_conversations(chat.scope, limit=50):
            self.history.addItem(conversation.title, conversation.id)
        self.history.setCurrentIndex(max(0, self.history.findData(chat.conversation.id)))
        self.history.blockSignals(False)
        self.send_button.setText("停止 ■" if chat.running else "发送 ↑")
        self.send_button.setEnabled(chat.running or provider_ready)
        self.regenerate_button.setEnabled(not chat.running and provider_ready)
        self._clear_layout(self.messages)
        self._cells.clear()
        self._tool_card_ids.clear()
        self._pending_text.clear()
        turns = chat.store.list_turns(chat.conversation.id, scope=chat.scope, limit=5)
        self._oldest_sequence = turns[0].sequence if turns else None
        for turn in turns:
            for item in chat.store.list_items(turn.id, scope=chat.scope, limit=100):
                self._add_item(item)
            if turn.status in ("interrupted", "cancelled", "failed"):
                label = QLabel("上一次回答已中断，可重新请求。" if turn.status == "interrupted" else "本轮已停止。" if turn.status == "cancelled" else "本轮生成失败。")
                label.setWordWrap(True)
                self.messages.addWidget(label)
        self.more.setVisible(bool(self._oldest_sequence and self._oldest_sequence > 1))
        self.refresh_pins()
        if self.isVisible() and chat.running:
            self.render_timer.start()

    def _reload_provider_selector(self, chat: AiChatService) -> bool:
        configs: list[ProviderConfig] = []
        for provider_id, record in self.business.store.list("ai_service"):
            try:
                config = ProviderConfig.from_record(record)
            except ValueError:
                continue
            if config.id == provider_id:
                configs.append(config)
        selected = chat.conversation.provider_id
        default_id = ""
        if not selected:
            try:
                default = self.business.chat_provider("")
                default_id = default.id if default else ""
            except ValueError:
                pass
            selected = default_id or (configs[0].id if configs else "")
        self.providers.blockSignals(True)
        self.providers.clear()
        if not configs:
            self.providers.addItem("未配置 AI 服务", None)
        elif selected not in {config.id for config in configs}:
            self.providers.addItem("会话绑定的 AI 服务已不可用", None)
        for config in configs:
            model = chat.conversation.model if config.id == chat.conversation.provider_id and chat.conversation.model else config.model
            prefix = "默认 · " if not chat.conversation.provider_id and config.id == default_id else ""
            self.providers.addItem(f"{prefix}{config.name} · {model}", config.id)
        self.providers.setCurrentIndex(max(0, self.providers.findData(selected)))
        self.providers.blockSignals(False)
        self.providers.setEnabled(bool(configs) and not chat.running)
        return isinstance(self.providers.currentData(), str) and bool(self.providers.currentData())

    @staticmethod
    def _clear_layout(layout: QVBoxLayout | QHBoxLayout) -> None:
        while item := layout.takeAt(0):
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_item(self, item: Item, index: int | None = None) -> None:
        if item.id in self._cells:
            return
        if item.kind == "tool_result":
            card_id = self._tool_card_ids.get(str(item.payload.get("call_id", "")), "")
            card = self._cells.get(card_id)
            if isinstance(card, AiToolCard):
                card.complete(validate_payload(item.payload.get("result")))
            return
        cell: QWidget
        if item.kind in ("user_message", "assistant_message"):
            cell = AiMessageCell(item.text, user=item.kind == "user_message")
            if item.kind == "assistant_message":
                cell.set_text(item.text, final=item.status != "running")
                if not item.text and item.status != "running":
                    cell.hide()
        elif item.kind == "tool_call":
            name = item.payload.get("name", "工具")
            cell = AiToolCard(str(name))
            self._tool_card_ids[str(item.payload.get("call_id", ""))] = item.id
            if item.status != "running":
                cell.toggle.setText("▸ " + cell.toggle.text()[2:].split(" · ")[0] + (" · ✓" if item.status == "complete" else " · 未完成"))
        elif item.kind == "indicator_draft":
            scope = self.chat.scope if self.chat is not None else ""
            conversation = self.chat.conversation.id if self.chat is not None else ""
            def preview(source: str) -> None:
                self._run(lambda: self.preview_indicator(source, scope, conversation))
            cell = AiIndicatorDraftCard(item.payload, preview)
        elif item.kind == "order_draft":
            draft = OrderDraft.from_ai(item.payload.get("draft"))
            scope = self.chat.scope if self.chat is not None else ""
            conversation_id = self.chat.conversation.id if self.chat is not None else ""
            def load(value: OrderDraft) -> None:
                if self.chat is not None and self.business.scope == scope and self.chat.conversation.id == conversation_id:
                    self.draft_ready.emit(value)
            cell = AiOrderDraftCard(draft, load)
        else:
            label = QLabel(item.text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setObjectName("muted")
            cell = label
        self._cells[item.id] = cell
        if index is None:
            self.messages.addWidget(cell)
        else:
            self.messages.insertWidget(index, cell)

    def preview_indicator(self, source: str, scope: str, conversation: str) -> None:
        def same_conversation() -> bool:
            return (not self.business.closed and self.business.scope == scope and self.chat is not None
                    and self.chat.conversation.id == conversation)
        if not same_conversation() or self.chart_provider is None:
            raise ValueError("请在当前会话和图表中打开指标草稿")
        panel = self.chart_provider()
        pair = panel.instrument, panel.bar
        def can_apply() -> bool:
            return same_conversation() and self.chart_provider is not None and self.chart_provider() is panel and pair == (panel.instrument, panel.bar)
        def apply(instance: IndicatorInstance) -> None:
            if not can_apply():
                raise ValueError("当前图表已变化，请重新预览")
            panel.indicator_book.save_indicators([*panel.indicator_book.indicators, instance_record(instance)])
        editor = IndicatorEditor(self.business, apply, self, source=source, language="ai",
            preview_pair=pair, preview_rows=lambda: CandleBuffer(panel.canvas.rows), can_apply=can_apply)
        editor.exec()

    def on_event(self, event: AiEvent) -> None:
        try:
            self._handle_event(event)
        except sqlite3.Error:
            if self.chat is not None:
                self.chat.handle_storage_error()

    def _handle_event(self, event: AiEvent) -> None:
        chat = self.chat
        if chat is None or self.business.closed or event.conversation_id != chat.conversation.id:
            return
        if event.item_id is not None and event.item_id not in self._cells:
            item = chat.store.get_item(event.item_id, scope=chat.scope)
            if item is not None:
                self._add_item(item)
        if event.kind == "item.delta" and event.item_id is not None:
            cell = self._cells.get(event.item_id)
            if isinstance(cell, AiMessageCell):
                self._pending_text[event.item_id] = self._pending_text.get(event.item_id, cell.message_text) + str(event.payload.get("text", ""))
                if self.isVisible() and not self.render_timer.isActive():
                    self.render_timer.start()
        elif event.kind == "item.completed" and event.item_id is not None:
            cell = self._cells.get(event.item_id)
            if isinstance(cell, AiMessageCell):
                self._pending_text.pop(event.item_id, None)
                cell.set_text(str(event.payload.get("text", cell.message_text)), final=True)
                if not cell.message_text:
                    cell.hide()
        elif event.kind == "tool.completed" and event.item_id is not None:
            cell = self._cells.get(event.item_id)
            if isinstance(cell, AiToolCard):
                cell.complete(validate_payload(event.payload.get("result")))
        elif event.kind in ("turn.completed", "turn.failed", "turn.cancelled"):
            self.render_timer.stop()

    def flush_text(self) -> None:
        if not self.isVisible():
            self.render_timer.stop()
            return
        bar = require(self.message_scroll.verticalScrollBar())
        at_bottom = bar.maximum() - bar.value() <= 30
        pending, self._pending_text = self._pending_text, {}
        for key, text in pending.items():
            cell = self._cells.get(key)
            if isinstance(cell, AiMessageCell):
                cell.set_text(text)
        if at_bottom:
            bar.setValue(bar.maximum())

    def choose_provider(self, _index: int) -> None:
        key = self.providers.currentData()
        chat = self.chat
        if chat is not None and key:
            previous = chat.conversation.id
            def configure() -> None:
                config = self.business.chat_provider(key)
                if config is not None:
                    chat.configure(config)
            self._run(configure)
            if chat.conversation.id != previous:
                self.feedback.setText("已为新的 AI 服务创建新对话。")
        elif chat is not None:
            self.reload()

    def choose_history(self, _index: int) -> None:
        chat = self.chat
        key = self.history.currentData()
        if chat is not None and key and key != chat.conversation.id:
            self._run(lambda: chat.open_conversation(key))

    def new_chat(self) -> None:
        if self.chat is not None:
            key = self.providers.currentData() or ""
            self._run(lambda: self.chat.new_conversation(key) if self.chat is not None else None)

    def send_or_stop(self) -> None:
        if self.chat is not None and self.chat.running:
            self.stop()
        else:
            self.send()

    def send(self) -> None:
        chat = self.chat
        if chat is None or chat.running:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        def submit() -> None:
            key = self.providers.currentData()
            if not isinstance(key, str) or not key:
                raise ValueError("请先配置并选择可用的 AI 服务")
            if key and chat.conversation.provider_id != key:
                config = self.business.chat_provider(key)
                if config is not None:
                    chat.configure(config)
            preset = self._presets[self.preset.currentIndex()]
            chat.send(text, self.context_provider(), preset)
            self.input.clear()
        self._run(submit)

    def stop(self) -> None:
        if self.chat is not None:
            self.chat.cancel()

    def regenerate(self) -> None:
        if self.chat is not None:
            self._run(lambda: self.chat.regenerate(self.context_provider(), self._presets[self.preset.currentIndex()]) if self.chat is not None else None)

    def pin_chart(self) -> None:
        def add() -> None:
            snapshot = self.context_provider()
            chart = snapshot.active_chart
            if chart is not None:
                self.pin("chart", chart.instrument + "/" + chart.bar, chart.instrument.replace("-USDT-SWAP", "") + " · " + chart.bar)
        self._run(add)

    def pin(self, kind: PinKind, reference: str, label: str) -> None:
        if self.chat is not None:
            self.chat.store.add_pin(self.chat.conversation.id, scope=self.chat.scope, kind=kind, reference=reference, label=label)
            self.refresh_pins()

    def refresh_pins(self) -> None:
        self._clear_layout(self.pin_layout)
        chat = self.chat
        if chat is not None:
            for pin in chat.store.list_pins(chat.conversation.id, scope=chat.scope):
                chip = QPushButton(pin.label[:22] + " ×")
                chip.setToolTip(pin.label)
                chip.clicked.connect(lambda checked=False, key=pin.id: self.remove_pin(key))
                self.pin_layout.addWidget(chip)
        self.pin_layout.addStretch()

    def remove_pin(self, pin_id: str) -> None:
        if self.chat is not None:
            self._run(lambda: self.chat.store.remove_pin(pin_id, scope=self.chat.scope) if self.chat is not None else None)
            self.refresh_pins()

    def load_more(self) -> None:
        chat = self.chat
        if chat is None or self._oldest_sequence is None:
            return
        turns = chat.store.list_turns(chat.conversation.id, scope=chat.scope, limit=5, before_sequence=self._oldest_sequence)
        offset = 0
        for turn in turns:
            for item in chat.store.list_items(turn.id, scope=chat.scope, limit=100):
                self._add_item(item, offset)
                offset += item.kind != "tool_result"
        if turns:
            self._oldest_sequence = turns[0].sequence
        self.more.setVisible(bool(turns and self._oldest_sequence > 1))

    @override
    def hideEvent(self, a0: QHideEvent | None) -> None:
        self.render_timer.stop()
        super().hideEvent(a0)

    @override
    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self.flush_text()
        if self.chat is not None and self.chat.running:
            self.render_timer.start()
