from collections.abc import Iterator
import json
import sqlite3
from pathlib import Path
import time
from dataclasses import replace

from PyQt6.QtCore import QObject
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from coinpilot_ai.ai.context import ChartContext, WorkspaceSnapshot, estimate_tokens
from coinpilot_ai.ai.engine import AiChatService
from coinpilot_ai.ai.events import AiEvent
from coinpilot_ai.ai.models import Item, JsonValue, TurnStatus
from coinpilot_ai.ai.prompts import COMPACTION_INSTRUCTIONS
from coinpilot_ai.ai.providers.base import (
    ModelRequest, ProviderCallback, ProviderCompleted, ProviderConfig, ProviderEvent,
    ProviderFailed, TextDelta, ToolCall, ToolRequested,
)
from coinpilot_ai.ai.store import AiChatStore
from coinpilot_ai.ai.tools.base import ToolCallback, ToolContext, ToolDefinition, ToolResult
from coinpilot_ai.ai.tools.market import immediate, schema
from coinpilot_ai.ai.tools.registry import ToolRegistry
from coinpilot_ai.ai.transport import RequestHandle

SCOPE = "demo:engine"


class ManualRunner:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.callbacks: list[ProviderCallback] = []
        self.cancelled: list[int] = []
        self.timeouts: list[int] = []

    def start(self, config: ProviderConfig, request: ModelRequest, callback: ProviderCallback, *, timeout_ms: int) -> RequestHandle:
        index = len(self.requests)
        self.requests.append(request)
        self.timeouts.append(timeout_ms)
        self.callbacks.append(callback)
        return RequestHandle(str(index), lambda: self.cancelled.append(index))

    def emit(self, event: ProviderEvent, index: int = -1) -> None:
        self.callbacks[index](event)


def snapshot(scope: str = SCOPE) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(time.time(), scope, scope.split(":")[0], ChartContext("BTC-USDT-SWAP", "1H"))


@pytest.fixture
def chat(tmp_path: Path, app: QApplication) -> Iterator[tuple[AiChatService, ManualRunner]]:
    runner = ManualRunner()
    registry = ToolRegistry(lambda: service.scope)
    config = ProviderConfig("test", "Test", "chat", "http://localhost", "fake-model")
    service = AiChatService(AiChatStore(tmp_path / "chat.sqlite3"), runner, SCOPE, lambda key: config, registry)
    try:
        yield service, runner
    finally:
        service.close()
        registry.deleteLater()
        service.deleteLater()
        app.processEvents()


def test_multi_turn_text_and_snapshot_and_events(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    events: list[AiEvent] = []
    service.event_received.connect(events.append)
    first = service.send("BTC 1H 怎么样", snapshot())
    runner.emit(TextDelta("先观察"))
    runner.emit(TextDelta("趋势"))
    runner.emit(ProviderCompleted())
    assert not service.running
    completed = service.store.get_turn(first.id, scope=SCOPE)
    assert completed is not None and completed.status == "complete"
    service.send("那 4H 呢", snapshot())
    texts = [message.text for message in runner.requests[-1].messages]
    assert "BTC 1H 怎么样" in texts and "先观察趋势" in texts and "那 4H 呢" in texts
    assert any("active_chart" in text for text in texts)
    assert not any("positions" in text or "candles" in text for text in texts)
    assert any(event.kind == "item.delta" for event in events)
    assert any(event.kind == "turn.completed" for event in events)


def test_cancel_scope_change_and_late_callbacks_do_not_mutate(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    turn = service.send("问题", snapshot())
    runner.emit(TextDelta("部分回答"))
    service.cancel()
    runner.emit(TextDelta("迟到回答"), 0)
    runner.emit(ProviderCompleted(), 0)
    saved = service.store.get_turn(turn.id, scope=SCOPE)
    assert saved is not None and saved.status == "cancelled"
    assistant = [item for item in service.store.list_items(turn.id, scope=SCOPE) if item.kind == "assistant_message"][0]
    assert assistant.text == "部分回答" and assistant.status == "cancelled"
    service.set_scope("live:engine")
    live = service.send("新环境", snapshot("live:engine"))
    runner.emit(TextDelta("旧环境数据"), 0)
    runner.emit(TextDelta("新的回答"))
    runner.emit(ProviderCompleted())
    items = service.store.list_items(live.id, scope="live:engine")
    assert all("旧环境" not in item.text for item in items)
    assert service.store.list_conversations("live:engine")[0].id != turn.conversation_id


def test_provider_failure_and_timeout_keep_partial_text(chat: tuple[AiChatService, ManualRunner], app: QApplication) -> None:
    service, runner = chat
    first = service.send("问题", snapshot())
    runner.emit(TextDelta("部分"))
    runner.emit(ProviderFailed("模拟失败"))
    saved = service.store.get_turn(first.id, scope=SCOPE)
    assert saved is not None and saved.status == "failed" and saved.error == "模拟失败"
    service.turn_timeout_ms = 10
    second = service.send("超时问题", snapshot())
    QTest.qWait(30)
    app.processEvents()
    saved = service.store.get_turn(second.id, scope=SCOPE)
    assert saved is not None and saved.status == "failed"


def test_serial_tool_round_and_unknown_tool_returns_to_model(chat: tuple[AiChatService, ManualRunner], app: QApplication) -> None:
    service, runner = chat
    executed: list[str] = []
    def handler(context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        executed.append("market")
        return immediate(lambda: ToolResult(True, time.time(), "fake", "live", (), {"price": "100"}), callback)
    service.registry.register(ToolDefinition("market", "市场", schema({}), "market", handler))
    turn = service.send("读取行情", snapshot())
    runner.emit(ToolRequested(ToolCall("first", "market", {})))
    runner.emit(ToolRequested(ToolCall("second", "does_not_exist", {})))
    runner.emit(ProviderCompleted())
    assert len(runner.requests) == 1
    for _ in range(50):
        app.processEvents()
        QTest.qWait(2)
        if len(runner.requests) == 2:
            break
    assert executed == ["market"] and len(runner.requests) == 2
    messages = runner.requests[-1].messages
    outputs = [json.loads(message.text) for message in messages if message.role == "tool"]
    assert outputs[0]["ok"] is True and outputs[1]["ok"] is False
    runner.emit(TextDelta("说明数据缺口"))
    runner.emit(ProviderCompleted())
    items = service.store.list_items(turn.id, scope=SCOPE)
    assert [item.kind for item in items].count("tool_call") == 2
    assert [item.kind for item in items].count("tool_result") == 2
    assert not service.running


def test_tool_call_limit_prevents_execution(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    turn = service.send("过多调用", snapshot())
    for index in range(21):
        runner.emit(ToolRequested(ToolCall(str(index), "market", {})))
    runner.emit(ProviderCompleted())
    saved = service.store.get_turn(turn.id, scope=SCOPE)
    assert saved is not None and saved.status == "failed" and "上限" in saved.error
    assert not any(item.kind == "tool_call" for item in service.store.list_items(turn.id, scope=SCOPE))


def test_indicator_draft_is_persisted_as_separate_item(chat: tuple[AiChatService, ManualRunner], app: QApplication) -> None:
    service, runner = chat
    data: dict[str, JsonValue] = {"source": 'indicator("x")\nplot(close)', "valid": True, "requires_confirmation": True}
    def handler(context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        return immediate(lambda: ToolResult(True, time.time(), "indicator_compiler", "historical", (), data), callback)
    service.registry.register(ToolDefinition("create_indicator_draft", "指标草稿", schema({}), "market", handler))
    turn = service.send("创建指标", snapshot())
    runner.emit(ToolRequested(ToolCall("indicator", "create_indicator_draft", {})))
    runner.emit(ProviderCompleted())
    for _ in range(60):
        app.processEvents()
        QTest.qWait(2)
        if len(runner.requests) == 2:
            break
    runner.emit(ProviderCompleted())
    drafts = [item for item in service.store.list_items(turn.id, scope=SCOPE) if item.kind == "indicator_draft"]
    assert len(drafts) == 1 and drafts[0].payload == data


def test_auto_compaction_excludes_tool_facts_and_persists_summary(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    for index in range(13):
        turn = service.store.start_turn(service.conversation.id, f"问题 {index}", scope=SCOPE)
        service.store.append_item(turn.id, scope=SCOPE, kind="assistant_message", text="讨论风险管理")
        service.store.append_item(turn.id, scope=SCOPE, kind="context_notice", payload={"account_balance": "sensitive-temporal-fact"})
        service.store.finish_turn(turn.id, scope=SCOPE)
    service.send("继续讨论", snapshot())
    assert runner.requests[0].system.endswith(COMPACTION_INSTRUCTIONS)
    assert not any("sensitive-temporal-fact" in message.text for message in runner.requests[0].messages)
    summary = json.dumps({"intent": "控制风险", "questions": [], "plans": [], "conclusions": ["需要止损"], "pending": ["讨论风险比例"]}, ensure_ascii=False)
    runner.emit(TextDelta(summary))
    runner.emit(ProviderCompleted())
    assert len(runner.requests) == 2
    saved = service.store.get_conversation(service.conversation.id, scope=SCOPE)
    assert saved is not None and saved.summary_until_turn == 9 and "控制风险" in saved.summary
    runner.emit(TextDelta("新的分析"))
    runner.emit(ProviderCompleted())
    assert not service.running


def test_unconfigured_service_and_snapshot_scope_mismatch_are_rejected(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    service.config_provider = lambda key: None
    with pytest.raises(ValueError, match="配置 AI"):
        service.send("问题", snapshot())
    with pytest.raises(ValueError, match="环境"):
        service.send("问题", snapshot("live:elsewhere"))
    assert runner.requests == []


def test_duplicate_tool_ids_stop_before_execution(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    turn = service.send("问题", snapshot())
    runner.emit(ToolRequested(ToolCall("same", "market", {})))
    runner.emit(ToolRequested(ToolCall("same", "market", {})))
    runner.emit(ProviderCompleted())
    saved = service.store.get_turn(turn.id, scope=SCOPE)
    assert saved is not None and saved.status == "failed" and "标识重复" in saved.error
    assert not any(item.kind == "tool_call" for item in service.store.list_items(turn.id, scope=SCOPE))


def test_tool_round_limit_and_failed_call_history_keep_protocol_pairs(chat: tuple[AiChatService, ManualRunner], app: QApplication) -> None:
    service, runner = chat
    turn = service.send("检查缺口", snapshot())
    for index in range(9):
        runner.emit(ToolRequested(ToolCall(f"unknown-{index}", "unknown", {})))
        runner.emit(ProviderCompleted())
        QTest.qWait(5)
        app.processEvents()
    saved = service.store.get_turn(turn.id, scope=SCOPE)
    assert saved is not None and saved.status == "failed" and "上限" in saved.error
    assert len(runner.requests) == 9
    service.send("继续", snapshot())
    runner.emit(ToolRequested(ToolCall("unknown-complete", "unknown", {})))
    runner.emit(ProviderCompleted())
    QTest.qWait(10)
    runner.emit(TextDelta("缺少数据"))
    runner.emit(ProviderCompleted())
    service.send("追问", snapshot())
    messages = runner.requests[-1].messages
    assert any(call.id == "unknown-complete" for message in messages for call in message.tool_calls)
    assert any(message.tool_call_id == "unknown-complete" for message in messages)


def test_tool_continuation_compacts_history_without_losing_current_call(chat: tuple[AiChatService, ManualRunner], app: QApplication) -> None:
    service, runner = chat
    config = ProviderConfig("test", "Test", "chat", "http://localhost", "fake", context_window=6000, max_output_tokens=256)
    service.config_provider = lambda key: config
    for index in range(8):
        old = service.store.start_turn(service.conversation.id, f"question {index}", scope=SCOPE)
        service.store.append_item(old.id, scope=SCOPE, kind="assistant_message", text="discussion " * 40)
        service.store.finish_turn(old.id, scope=SCOPE)
    result_text = [""]
    def handler(context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        return immediate(lambda: ToolResult(True, time.time(), "test", "historical", (), {"rows": result_text[0]}), callback)
    service.registry.register(ToolDefinition("market", "market", schema({}), "market", handler))
    service.send("继续", snapshot())
    assert not runner.requests[-1].system.endswith(COMPACTION_INSTRUCTIONS)
    size = int((config.context_window * .72 - estimate_tokens(runner.requests[-1])) * 2)
    assert 0 < size < 4500
    result_text[0] = "x" * size
    runner.emit(ToolRequested(ToolCall("current-call", "market", {})))
    runner.emit(ProviderCompleted())
    QTest.qWait(10)
    app.processEvents()
    assert len(runner.requests) == 2 and runner.requests[-1].system.endswith(COMPACTION_INSTRUCTIONS)
    assert not any(result_text[0] in message.text for message in runner.requests[-1].messages)
    summary = json.dumps({"intent": "继续讨论", "questions": [], "plans": [], "conclusions": [], "pending": []}, ensure_ascii=False)
    runner.emit(TextDelta(summary))
    runner.emit(ProviderCompleted())
    assert len(runner.requests) == 3
    assert any(call.id == "current-call" for message in runner.requests[-1].messages for call in message.tool_calls)
    assert any(message.tool_call_id == "current-call" and result_text[0] in message.text for message in runner.requests[-1].messages)
    runner.emit(TextDelta("完成"))
    runner.emit(ProviderCompleted())


def test_large_tool_result_returns_explicit_budget_gap_to_model(chat: tuple[AiChatService, ManualRunner], app: QApplication) -> None:
    service, runner = chat
    base = service.config_provider("")
    assert base is not None
    service.config_provider = lambda key: replace(base, context_window=4096, max_output_tokens=256)
    def handler(context: ToolContext, arguments: dict[str, JsonValue], callback: ToolCallback) -> RequestHandle:
        return immediate(lambda: ToolResult(True, time.time(), "test", "historical", (), {"rows": "x" * 10000}), callback)
    service.registry.register(ToolDefinition("market", "market", schema({}), "market", handler))
    turn = service.send("读取", snapshot())
    runner.emit(ToolRequested(ToolCall("large", "market", {})))
    runner.emit(ProviderCompleted())
    QTest.qWait(10)
    assert len(runner.requests) == 2
    output = next(message for message in runner.requests[-1].messages if message.role == "tool")
    assert "上下文预算" in output.text and "x" * 10000 not in output.text
    items = service.store.list_items(turn.id, scope=SCOPE)
    assert next(item for item in items if item.kind == "tool_call").status == "failed"


@pytest.mark.parametrize("operation", ["complete", "cancel", "checkpoint"])
def test_sqlite_write_failure_stops_only_chat_and_allows_scope_change(chat: tuple[AiChatService, ManualRunner],
                                                                     app: QApplication, operation: str) -> None:
    service, runner = chat
    turn = service.send("模拟磁盘故障", snapshot())
    runner.emit(TextDelta("部分文本"))
    original = service.store.update_item
    def broken(item_id: str, *, scope: str, text: str | None = None,
               payload: dict[str, JsonValue] | None = None, status: TurnStatus | None = None) -> Item:
        raise sqlite3.OperationalError("fake disk full with private path")
    service.store.update_item = broken
    if operation == "complete":
        runner.emit(ProviderCompleted())
    elif operation == "cancel":
        service.cancel()
    else:
        QTest.qWait(550)
    assert not service.running and service.storage_error
    assert "private path" not in service.storage_error
    service.set_scope("live:new")
    assert service.scope == "live:new"
    with pytest.raises(ValueError, match="存储读写失败"):
        service.send("新问题", snapshot("live:new"))
    runner.emit(TextDelta("迟到"), 0)
    service.store.update_item = original
    saved = service.store.get_turn(turn.id, scope=SCOPE)
    assert saved is not None and saved.status == "running"  # 下次启动由恢复流程标记 interrupted。


def test_first_send_binds_default_and_existing_conversation_keeps_model(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    first = ProviderConfig("a", "A", "chat", "http://localhost", "model-a")
    second = replace(first, id="b", name="B", model="model-b")
    configs = {"a": first, "b": second}
    default = ["a"]
    service.config_provider = lambda key: configs.get(key or default[0])
    service.send("第一个问题", snapshot())
    runner.emit(TextDelta("A 的回答"))
    runner.emit(ProviderCompleted())
    conversation_id = service.conversation.id
    assert (service.conversation.provider_id, service.conversation.model) == ("a", "model-a")
    default[0] = "b"
    configs["a"] = replace(first, model="changed-global-model")
    service.send("同一会话继续", snapshot())
    assert runner.requests[-1].model == "model-a" and service.conversation.id == conversation_id
    runner.emit(ProviderCompleted())
    service.configure(second)
    assert service.conversation.id != conversation_id and service.conversation.provider_id == "b"
    service.send("新的服务", snapshot())
    assert runner.requests[-1].model == "model-b"
    assert not any("A 的回答" in message.text for message in runner.requests[-1].messages)
    assert service.store.list_turns(conversation_id, scope=SCOPE)


def test_regenerate_uses_original_history_boundary_and_keeps_both_versions(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    service.send("先前问题", snapshot())
    runner.emit(TextDelta("先前上下文"))
    runner.emit(ProviderCompleted())
    original = service.send("目标问题", snapshot())
    runner.emit(TextDelta("first-version-answer"))
    runner.emit(ProviderCompleted())
    regenerated = service.regenerate(snapshot())
    messages = runner.requests[-1].messages
    assert any(message.text == "先前上下文" for message in messages)
    assert sum(message.text == "目标问题" for message in messages) == 1
    assert not any("first-version-answer" in message.text for message in messages)
    runner.emit(TextDelta("second-version-answer"))
    runner.emit(ProviderCompleted())
    service.regenerate(snapshot())
    assert not any("version-answer" in message.text for message in runner.requests[-1].messages)
    saved = service.store.get_turn(original.id, scope=SCOPE)
    assert saved is not None and saved.status == "complete"
    assert any(item.text == "first-version-answer" for item in service.store.list_items(original.id, scope=SCOPE))
    assert any(item.text == "second-version-answer" for item in service.store.list_items(regenerated.id, scope=SCOPE))


def test_model_and_tool_rounds_share_remaining_deadline(chat: tuple[AiChatService, ManualRunner], app: QApplication) -> None:
    service, runner = chat
    service.send("多轮读取", snapshot())
    assert 179000 < runner.timeouts[0] <= 180000
    QTest.qWait(30)
    runner.emit(ToolRequested(ToolCall("unknown-one", "unknown", {})))
    runner.emit(ProviderCompleted())
    QTest.qWait(15)
    assert len(runner.requests) == 2 and runner.timeouts[1] < runner.timeouts[0] - 20
    QTest.qWait(30)
    runner.emit(ToolRequested(ToolCall("unknown-two", "unknown", {})))
    runner.emit(ProviderCompleted())
    QTest.qWait(15)
    assert len(runner.requests) == 3 and runner.timeouts[2] < runner.timeouts[1] - 20


def test_regenerate_compaction_excludes_target_and_does_not_rewind_saved_summary(chat: tuple[AiChatService, ManualRunner]) -> None:
    service, runner = chat
    for index in range(15):
        turn = service.store.start_turn(service.conversation.id, f"question-{index}", scope=SCOPE)
        service.store.append_item(turn.id, scope=SCOPE, kind="assistant_message", text=f"answer-{index}")
        service.store.finish_turn(turn.id, scope=SCOPE)
    service.conversation = service.store.set_summary(service.conversation.id, "contaminated-answer-14", 15, scope=SCOPE)
    service.regenerate(snapshot())
    assert runner.requests[-1].system.endswith(COMPACTION_INSTRUCTIONS)
    assert not any("answer-14" in message.text for message in runner.requests[-1].messages)
    summary = json.dumps({"intent": "早期讨论", "questions": [], "plans": [], "conclusions": [], "pending": []}, ensure_ascii=False)
    runner.emit(TextDelta(summary))
    runner.emit(ProviderCompleted())
    assert len(runner.requests) == 2
    assert not any("answer-14" in message.text for message in runner.requests[-1].messages)
    assert runner.requests[-1].messages[-1].text == "question-14"
    saved = service.store.get_conversation(service.conversation.id, scope=SCOPE)
    assert saved is not None and saved.summary_until_turn == 15
