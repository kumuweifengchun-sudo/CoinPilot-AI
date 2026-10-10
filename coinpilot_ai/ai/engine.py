"""会话运行层：流式公开输出、串行工具轮次、检查点、压缩与取消。"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import json
import sqlite3
import time
from typing import Protocol

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .context import CompactionTask, ContextBuilder, WorkspaceSnapshot, parse_summary
from .events import AiEvent, AiEventKind
from .models import Conversation, JsonValue, Turn, validate_integer
from .presets import PRESETS, Preset
from .providers.base import (
    ModelMessage, ModelRequest, ProviderCallback, ProviderConfig, ProviderEvent, ToolCall,
)
from .store import AiChatStore
from .tools.base import ToolContext, ToolResult
from .tools.registry import ToolRegistry
from .transport import RequestHandle


class ModelRunner(Protocol):
    def start(self, config: ProviderConfig, request: ModelRequest, callback: ProviderCallback, *, timeout_ms: int) -> RequestHandle: ...


@dataclass
class _Runtime:
    turn: Turn
    config: ProviderConfig
    snapshot: WorkspaceSnapshot
    preset: Preset
    request: ModelRequest
    timeout: QTimer
    checkpoint: QTimer
    deadline: float
    handle: RequestHandle | None = None
    tool_handle: RequestHandle | None = None
    assistant_id: str | None = None
    text: str = ""
    saved_text: str = ""
    compact_text: str = ""
    compaction: CompactionTask | None = None
    calls: list[ToolCall] = field(default_factory=list)
    tool_items: list[str] = field(default_factory=list)
    next_tool: int = 0
    total_calls: int = 0
    rounds: int = 0
    generation: int = 0
    continuation: tuple[ModelMessage, ...] = ()
    call_ids: set[str] = field(default_factory=set)
    history_before_sequence: int | None = None
    history_conversation: Conversation | None = None


class AiChatService(QObject):
    # QObject.event() 是 Qt 原有接口，业务信号不能覆盖它。
    event_received = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(
        self, store: AiChatStore, manager: ModelRunner, scope: str,
        config_provider: Callable[[str], ProviderConfig | None], registry: ToolRegistry,
        parent: QObject | None = None, *, turn_timeout_ms: int = 180000,
    ) -> None:
        super().__init__(parent)
        self.store, self.manager, self.scope = store, manager, scope
        self.config_provider, self.registry = config_provider, registry
        self.context = ContextBuilder(store)
        self.turn_timeout_ms = turn_timeout_ms
        self.closed = False
        self.storage_error = ""
        self._active: _Runtime | None = None
        recent = store.list_conversations(scope, limit=1)
        self.conversation = recent[0] if recent else store.create_conversation(scope)

    @property
    def running(self) -> bool:
        return self._active is not None

    def new_conversation(self, provider_id: str = "", model: str = "") -> Conversation:
        self.cancel()
        self.conversation = self.store.create_conversation(self.scope, provider_id=provider_id, model=model)
        self.changed.emit()
        return self.conversation

    def open_conversation(self, conversation_id: str) -> None:
        conversation = self.store.get_conversation(conversation_id, scope=self.scope)
        if conversation is None:
            raise LookupError("当前环境不存在该会话")
        if self.conversation.id != conversation_id:
            self.cancel()
        self.conversation = conversation
        self.changed.emit()

    def configure(self, config: ProviderConfig) -> None:
        if (self.conversation.provider_id, self.conversation.model) != (config.id, config.model) and self.store.list_turns(
                self.conversation.id, scope=self.scope, limit=1):
            self.new_conversation(config.id, config.model)
            return
        self.conversation = self.store.update_conversation(
            self.conversation.id, scope=self.scope, provider_id=config.id, model=config.model,
        )
        self.changed.emit()

    def scope_changing(self) -> None:
        self.cancel()

    def set_scope(self, scope: str) -> None:
        self.cancel()
        self.scope = scope
        if self.storage_error:
            return
        try:
            recent = self.store.list_conversations(scope, limit=1)
            self.conversation = recent[0] if recent else self.store.create_conversation(scope)
        except sqlite3.Error:
            self.handle_storage_error()
            return
        self.changed.emit()

    def handle_storage_error(self) -> None:
        """停止聊天自身的任务，不让磁盘写入故障进入共享行情和交易生命周期。"""
        self.storage_error = "聊天存储读写失败，请检查磁盘后重启；行情与手工交易仍可使用"
        run = self._active
        if run is not None:
            self._dispose(run)
            self._emit(run, "turn.failed", payload={"error": self.storage_error})
        self.changed.emit()

    def _guard_storage(self, operation: Callable[[], object]) -> None:
        try:
            operation()
        except sqlite3.Error:
            self.handle_storage_error()

    def _emit(self, run: _Runtime, kind: AiEventKind, item_id: str | None = None,
              payload: dict[str, JsonValue] | None = None) -> None:
        self.event_received.emit(AiEvent(kind, run.turn.conversation_id, run.turn.id, item_id, payload or {}))

    def _alive(self, run: _Runtime, generation: int | None = None) -> bool:
        return (not self.closed and self._active is run and self.scope == run.snapshot.scope
                and (generation is None or run.generation == generation))

    def _tool_context(self, run: _Runtime) -> ToolContext:
        return ToolContext(self.scope, run.snapshot.environment, run.snapshot.captured_at,
                           run.config.allow_account, run.config.allow_history)

    def send(self, text: str, snapshot: WorkspaceSnapshot, preset: Preset = PRESETS[0]) -> Turn:
        return self._start_turn(text, snapshot, preset)

    def _start_turn(self, text: str, snapshot: WorkspaceSnapshot, preset: Preset, *,
                    history_before_sequence: int | None = None) -> Turn:
        if self.storage_error:
            raise ValueError(self.storage_error)
        if self.closed or self.running:
            raise ValueError("AI 服务已关闭或正在生成")
        if snapshot.scope != self.scope:
            raise ValueError("工作区环境已经改变，请重新发送")
        config = self.config_provider(self.conversation.provider_id)
        if config is None:
            raise ValueError("请在设置中配置 AI 服务并选择聊天模型")
        if self.conversation.provider_id and config.id != self.conversation.provider_id:
            raise ValueError("会话绑定的 AI 服务不可用，请选择服务创建新对话")
        if self.conversation.model and self.store.list_turns(self.conversation.id, scope=self.scope, limit=1):
            config = replace(config, model=self.conversation.model)
        self.conversation = self.store.update_conversation(self.conversation.id, scope=self.scope,
                                                         provider_id=config.id, model=config.model,
                                                         title=text.strip()[:40] if self.conversation.title == "新对话" else None)
        # 发送时捕获工作区，后续图表移动不会改变本轮工具时间或作用域。
        tool_context = ToolContext(self.scope, snapshot.environment, snapshot.captured_at,
                                   config.allow_account, config.allow_history)
        tools = self.registry.schemas(tool_context) if config.tools else ()
        request = self.context.build(self.conversation, config, snapshot, text, preset, tools,
                                     history_before_sequence=history_before_sequence)
        compact = self.context.compaction(self.conversation, config, request,
                                          history_before_sequence=history_before_sequence)
        if compact is None:
            self.context.ensure_budget(request, config)
        turn = self.store.start_turn(self.conversation.id, text, scope=self.scope)
        timeout, checkpoint = QTimer(self), QTimer(self)
        timeout.setSingleShot(True)
        checkpoint.setInterval(500)
        run = _Runtime(turn, config, snapshot, preset, request, timeout, checkpoint,
                       deadline=time.monotonic() + self.turn_timeout_ms / 1000, compaction=compact,
                       history_before_sequence=history_before_sequence)
        self._active = run
        timeout.timeout.connect(lambda: self._guard_storage(lambda: self._fail(run, "本轮 AI 请求超时")))
        checkpoint.timeout.connect(lambda: self._guard_storage(lambda: self._checkpoint(run)))
        timeout.start(self.turn_timeout_ms)
        checkpoint.start()
        self._emit(run, "turn.started")
        user_item = self.store.list_items(turn.id, scope=self.scope, limit=1)[0]
        self._emit(run, "item.completed", user_item.id, {"kind": "user_message", "text": text})
        notice = self.store.append_item(turn.id, scope=self.scope, kind="context_notice", text="已捕获当前工作区",
                                       payload={"workspace": snapshot.payload(), "preset": preset.id,
                                                "allow_account": config.allow_account, "allow_history": config.allow_history})
        if history_before_sequence is not None:
            self.store.append_item(turn.id, scope=self.scope, kind="context_notice",
                                   payload={"history_before_sequence": history_before_sequence})
        self._emit(run, "item.completed", notice.id, {"kind": notice.kind, "text": notice.text})
        if compact is not None:
            status = self.store.append_item(turn.id, scope=self.scope, kind="status", text="正在压缩历史讨论……")
            self._emit(run, "item.completed", status.id, {"kind": status.kind, "text": status.text})
            self._start_provider(run, compact.request)
        else:
            self._start_answer(run)
        self.changed.emit()
        return turn

    def _start_provider(self, run: _Runtime, request: ModelRequest) -> None:
        run.generation += 1
        generation = run.generation
        try:
            self.context.ensure_budget(request, run.config)
            remaining = self._remaining_ms(run)
            if remaining <= 0:
                self._fail(run, "本轮 AI 请求超时")
                return
            handle = self.manager.start(run.config, request,
                                        lambda event: self._guard_storage(lambda: self._provider_event(run, generation, event)),
                                        timeout_ms=remaining)
            if self._alive(run, generation):
                run.handle = handle
            else:
                handle.cancel()
        except (ValueError, OSError):
            self._fail(run, "AI 请求启动失败或上下文超过窗口，请检查服务设置")

    def _start_answer(self, run: _Runtime) -> None:
        if run.continuation:
            try:
                compact = self.context.compaction(run.history_conversation or self.conversation, run.config, run.request,
                                                  history_before_sequence=run.history_before_sequence)
            except ValueError:
                self._fail(run, "历史讨论过长，无法安全压缩，请开启新对话")
                return
            if compact is not None:
                run.compaction = compact
                status = self.store.append_item(run.turn.id, scope=self.scope, kind="status", text="正在压缩历史讨论……")
                self._emit(run, "item.completed", status.id, {"kind": status.kind, "text": status.text})
                self._start_provider(run, compact.request)
                return
        run.text, run.saved_text = "", ""
        run.calls.clear()
        run.tool_items.clear()
        run.next_tool = 0
        item = self.store.append_item(run.turn.id, scope=self.scope, kind="assistant_message", status="running")
        run.assistant_id = item.id
        self._emit(run, "item.started", item.id, {"kind": item.kind})
        self._start_provider(run, run.request)

    def _checkpoint(self, run: _Runtime) -> None:
        if self._alive(run) and run.assistant_id is not None and run.text != run.saved_text:
            self.store.update_item(run.assistant_id, scope=self.scope, text=run.text)
            run.saved_text = run.text

    @staticmethod
    def _remaining_ms(run: _Runtime) -> int:
        return max(0, int((run.deadline - time.monotonic()) * 1000))

    def _provider_event(self, run: _Runtime, generation: int, event: ProviderEvent) -> None:
        if not self._alive(run, generation):
            return
        if self._remaining_ms(run) <= 0:
            self._fail(run, "本轮 AI 请求超时")
            return
        if event.kind == "text_delta":
            if run.compaction is not None:
                run.compact_text += event.text
            else:
                run.text += event.text
                if run.assistant_id is not None:
                    self._emit(run, "item.delta", run.assistant_id, {"text": event.text})
        elif event.kind == "tool_call":
            if run.compaction is not None or not run.config.tools:
                self._fail(run, "当前请求不允许调用工具")
            else:
                run.calls.append(event.call)
        elif event.kind == "failed":
            if event.cancelled:
                self.cancel()
            else:
                self._fail(run, event.error)
        elif event.kind == "completed":
            run.handle = None
            if run.compaction is not None:
                self._compacted(run)
                return
            if run.assistant_id is not None:
                self.store.update_item(run.assistant_id, scope=self.scope, text=run.text, status="complete")
                self._emit(run, "item.completed", run.assistant_id, {"kind": "assistant_message", "text": run.text})
                run.assistant_id = None
            if not run.calls:
                self._finish(run)
            elif run.rounds >= 8 or run.total_calls + len(run.calls) > 20:
                self._fail(run, "已达到本轮工具调用上限，请缩小问题范围")
            elif len({call.id for call in run.calls}) != len(run.calls) or any(call.id in run.call_ids for call in run.calls):
                self._fail(run, "工具调用标识重复，已停止本轮请求")
            else:
                run.call_ids.update(call.id for call in run.calls)
                run.rounds += 1
                run.total_calls += len(run.calls)
                message = ModelMessage("assistant", run.text, tuple(run.calls))
                run.continuation = (*run.continuation, message)
                run.request = replace(run.request, messages=(*run.request.messages, message))
                for call in run.calls:
                    item = self.store.append_item(run.turn.id, scope=self.scope, kind="tool_call", status="running",
                                                  payload={"call_id": call.id, "name": call.name, "arguments": call.arguments})
                    run.tool_items.append(item.id)
                    self._emit(run, "tool.started", item.id, {"name": call.name})
                self._execute_next(run)

    def _compacted(self, run: _Runtime) -> None:
        compact = run.compaction
        if compact is None:
            return
        try:
            summary = parse_summary(run.compact_text)
            if compact.until_turn < self.conversation.summary_until_turn:
                # 重新生成较早的上下文时，摘要只属于本次请求，不回退已保存的全局摘要。
                run.history_conversation = replace(self.conversation, summary=summary, summary_until_turn=compact.until_turn,
                                                    summary_account=run.config.allow_account, summary_history=run.config.allow_history)
            else:
                self.conversation = self.store.set_summary(run.turn.conversation_id, summary, compact.until_turn, scope=self.scope,
                                                           account=run.config.allow_account, history=run.config.allow_history)
                run.history_conversation = self.conversation
            user_item = self.store.list_items(run.turn.id, scope=self.scope, limit=1)[0]
            run.request = self.context.build(run.history_conversation or self.conversation, run.config, run.snapshot, user_item.text, run.preset, run.request.tools,
                                             history_before_sequence=run.history_before_sequence)
            run.request = replace(run.request, messages=(*run.request.messages, *run.continuation))
            self.context.ensure_budget(run.request, run.config)
        except (ValueError, TypeError):
            self._fail(run, "历史摘要无效或压缩后仍超出上下文窗口，请开启新对话")
            return
        run.compaction = None
        run.compact_text = ""
        notice = self.store.append_item(run.turn.id, scope=self.scope, kind="context_notice", text="旧对话已压缩，当前行情与账户仍以最新工具为准")
        self._emit(run, "item.completed", notice.id, {"kind": notice.kind, "text": notice.text})
        self._start_answer(run)

    def _execute_next(self, run: _Runtime) -> None:
        if not self._alive(run):
            return
        remaining = self._remaining_ms(run)
        if remaining <= 0:
            self._fail(run, "本轮 AI 请求超时")
            return
        if run.next_tool >= len(run.calls):
            self._start_answer(run)
            return
        index = run.next_tool
        call = run.calls[index]
        handle = self.registry.execute(call, self._tool_context(run),
                                        lambda result: self._guard_storage(lambda: self._tool_result(run, index, result)),
                                        timeout_ms=remaining)
        if self._alive(run) and run.next_tool == index:
            run.tool_handle = handle
        else:
            handle.cancel()

    def _tool_result(self, run: _Runtime, index: int, result: ToolResult) -> None:
        if not self._alive(run) or index != run.next_tool:
            return
        if self._remaining_ms(run) <= 0:
            self._fail(run, "本轮 AI 请求超时")
            return
        call, item_id = run.calls[index], run.tool_items[index]
        run.tool_handle = None
        data = result.payload()
        message = ModelMessage("tool", json.dumps(data, ensure_ascii=False), tool_call_id=call.id)
        # 单个结果不得独占窗口，给其他工具和最终解释留出空间；缺口明确反馈给模型。
        isolated = replace(run.request, messages=(message,))
        try:
            self.context.ensure_budget(replace(isolated, max_output_tokens=run.config.context_window // 2), run.config)
        except ValueError:
            result = ToolResult.failure("结果超出当前上下文预算，请缩小工具范围")
            data = result.payload()
            message = ModelMessage("tool", json.dumps(data, ensure_ascii=False), tool_call_id=call.id)
        self.store.update_item(item_id, scope=self.scope, status="complete" if result.ok else "failed")
        self._emit(run, "tool.completed", item_id, {"name": call.name, "result": data})
        item = self.store.append_item(run.turn.id, scope=self.scope, kind="tool_result", payload={
            "call_id": call.id, "name": call.name, "result": data,
        })
        self._emit(run, "item.completed", item.id, {"kind": item.kind, "result": data})
        if call.name == "create_order_draft" and result.ok:
            draft = self.store.append_item(run.turn.id, scope=self.scope, kind="order_draft", payload=result.data)
            self._emit(run, "order_draft.created", draft.id, result.data)
        if call.name in ("create_indicator_draft", "apply_indicator_to_chart") and result.ok:
            draft = self.store.append_item(run.turn.id, scope=self.scope, kind="indicator_draft", payload=result.data)
            self._emit(run, "item.completed", draft.id, {"kind": draft.kind})
        run.continuation = (*run.continuation, message)
        run.request = replace(run.request, messages=(*run.request.messages, message))
        run.next_tool += 1
        QTimer.singleShot(0, lambda: self._guard_storage(lambda: self._execute_next(run)))

    def _dispose(self, run: _Runtime) -> None:
        self._active = None
        for timer in (run.timeout, run.checkpoint):
            timer.stop()
            timer.deleteLater()
        if run.handle is not None:
            run.handle.cancel()
        if run.tool_handle is not None:
            run.tool_handle.cancel()

    def _finish(self, run: _Runtime) -> None:
        if self._alive(run):
            self.store.finish_turn(run.turn.id, scope=self.scope)
            self._dispose(run)
            self._emit(run, "turn.completed")
            self.changed.emit()

    def _fail(self, run: _Runtime, error: str) -> None:
        if self._alive(run):
            self._checkpoint(run)
            item = self.store.append_item(run.turn.id, scope=self.scope, kind="error", text=error)
            self._emit(run, "item.completed", item.id, {"kind": "error", "text": error})
            self.store.finish_turn(run.turn.id, scope=self.scope, status="failed", error=error)
            self._dispose(run)
            self._emit(run, "turn.failed", payload={"error": error})
            self.changed.emit()

    def cancel(self) -> None:
        run = self._active
        if run is not None:
            try:
                self._checkpoint(run)
                self.store.finish_turn(run.turn.id, scope=run.snapshot.scope, status="cancelled")
            except sqlite3.Error:
                self.handle_storage_error()
                return
            self._dispose(run)
            self._emit(run, "turn.cancelled")
            self.changed.emit()

    def regenerate(self, snapshot: WorkspaceSnapshot, preset: Preset = PRESETS[0]) -> Turn:
        if self.running:
            raise ValueError("请先停止当前回答")
        turns = self.store.list_turns(self.conversation.id, scope=self.scope, limit=1)
        if not turns:
            raise ValueError("当前会话没有可重新生成的问题")
        user = self.store.list_items(turns[0].id, scope=self.scope, limit=1)[0]
        cutoff = turns[0].sequence
        for item in self.store.list_items(turns[0].id, scope=self.scope, limit=100):
            if item.kind == "context_notice" and "history_before_sequence" in item.payload:
                cutoff = validate_integer(item.payload["history_before_sequence"], "重新生成历史边界", minimum=1)
                if cutoff > turns[0].sequence:
                    raise ValueError("重新生成历史边界无效")
                break
        return self._start_turn(user.text, snapshot, preset, history_before_sequence=cutoff)

    def close(self) -> None:
        if not self.closed:
            self.cancel()
            self.closed = True
            self.store.close()
