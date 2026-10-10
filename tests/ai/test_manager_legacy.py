from pathlib import Path
import json
import time

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from coinpilot_ai.ai.context import WorkspaceSnapshot
from coinpilot_ai.ai.engine import AiChatService
from coinpilot_ai.ai.legacy import LegacyAiAdapter
from coinpilot_ai.ai.models import JsonValue
from coinpilot_ai.ai.providers.base import ModelMessage, ModelRequest, ProviderConfig, ProviderEvent, ProviderFailed
from coinpilot_ai.ai.providers.manager import ProviderManager
from coinpilot_ai.ai.store import AiChatStore
from coinpilot_ai.ai.tools.registry import ToolRegistry
from coinpilot_ai.ai.transport import FrameCallback, RequestHandle, StreamCallback, StreamError, StreamFrame
from coinpilot_ai.integrations.transport import ApiError


class FakeVault:
    def __init__(self, value: object) -> None:
        self.value = value
        self.names: list[str] = []

    def read(self, key: str) -> object:
        self.names.append(key)
        return self.value


class FakeTransport:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.body: dict[str, JsonValue] = {}
        self.frame: FrameCallback | None = None
        self.done: StreamCallback | None = None
        self.timeout = 0

    def post(self, url: str, headers: dict[str, str], body: dict[str, JsonValue],
             on_frame: FrameCallback, on_done: StreamCallback, *, timeout: int = 90000) -> RequestHandle:
        self.headers, self.body, self.frame, self.done = headers, body, on_frame, on_done
        self.timeout = timeout
        return RequestHandle("fake", lambda: on_done(None, StreamError("已取消", cancelled=True)))

    def finish_text(self, text: str) -> None:
        assert self.done is not None
        self.done({"choices": [{"finish_reason": "stop", "message": {"content": text}}]}, None)


def test_key_split_across_frames_never_reaches_items_database_or_logs(tmp_path: Path, app: QApplication, caplog: pytest.LogCaptureFixture) -> None:
    secret = "fake-unit-only-secret-123"
    vault, transport = FakeVault({"key": secret}), FakeTransport()
    config = ProviderConfig("test", "Test", "chat", "https://example.invalid/v1", "fake")
    scope = "demo:privacy"
    registry = ToolRegistry(lambda: scope)
    path = tmp_path / "chat.sqlite3"
    service = AiChatService(AiChatStore(path), ProviderManager(transport, vault), scope, lambda key: config, registry)
    try:
        turn = service.send("公开问题", WorkspaceSnapshot(time.time(), scope, "demo"))
        assert vault.names == ["ai/test"]
        assert transport.headers["Authorization"] == "Bearer " + secret
        assert secret not in json.dumps(transport.body)
        assert transport.frame is not None and transport.done is not None
        for part in ("前缀 ", secret[:4], secret[4:10], secret[10:], " 后缀"):
            transport.frame(StreamFrame(json.dumps({"choices": [{"delta": {"content": part}}]})))
        transport.frame(StreamFrame('{"choices":[{"delta":{},"finish_reason":"stop"}]}'))
        transport.frame(StreamFrame("[DONE]"))
        transport.done(None, None)
        items = service.store.list_items(turn.id, scope=scope)
        answer = next(item.text for item in items if item.kind == "assistant_message")
        assert answer == "前缀 [已隐藏凭据] 后缀"
        assert all(secret not in item.text and secret not in json.dumps(item.payload) for item in items)
        assert secret not in caplog.text
        for file in tmp_path.glob("chat.sqlite3*"):
            assert secret.encode() not in file.read_bytes()
    finally:
        service.close()
        service.deleteLater()
        registry.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("credentials", [None, [], {"key": 123}, {"key": ""}])
def test_invalid_remote_credentials_fail_asynchronously_without_post(credentials: object, app: QApplication) -> None:
    transport = FakeTransport()
    manager = ProviderManager(transport, FakeVault(credentials))
    config = ProviderConfig("test", "Test", "chat", "https://example.invalid", "fake")
    events: list[ProviderEvent] = []
    manager.start(config, ModelRequest("fake", "safety", (ModelMessage("user", "hello"),)), events.append)
    assert events == [] and transport.done is None
    QTest.qWait(5)
    assert len(events) == 1 and isinstance(events[0], ProviderFailed)


def test_local_service_without_key_and_legacy_model_and_draft_compatibility(app: QApplication) -> None:
    transport = FakeTransport()
    adapter = LegacyAiAdapter(ProviderManager(transport, FakeVault(None)))
    record = {"id": "test", "name": "Test", "protocol": "chat", "base_url": "http://localhost",
              "model": "legacy-model", "chat_model": "chat-model"}
    results: list[tuple[str | None, ApiError | None]] = []
    adapter.ask(record, "复盘问题", lambda text, error: results.append((text, error)), draft=True)
    assert transport.body["model"] == "legacy-model" and transport.body["stream"] is False
    assert "tools" not in transport.body and "OrderDraft" in json.dumps(transport.body)
    transport.finish_text("兼容报告")
    assert results == [("兼容报告", None)]
    identifier = adapter.ask(record, "再次分析", lambda text, error: results.append((text, error)))
    adapter.cancel(identifier)
    transport.finish_text("迟到报告")
    assert len(results) == 2 and results[-1][1] is not None and results[-1][1].cancelled
    adapter.close()


def test_tool_argument_credentials_are_scrubbed_before_publication(app: QApplication) -> None:
    secret = "fake-only-test-key"
    transport = FakeTransport()
    manager = ProviderManager(transport, FakeVault({"key": secret}))
    config = ProviderConfig("test", "Test", "chat", "https://example.invalid", "fake")
    events: list[ProviderEvent] = []
    manager.start(config, ModelRequest("fake", "safety", (ModelMessage("user", "hello"),)), events.append)
    assert transport.done is not None
    transport.done({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "call", "function": {"name": "market", "arguments": json.dumps({"api_key": secret, "note": secret})}},
    ]}}]}, None)
    call = next(event.call for event in events if event.kind == "tool_call")
    assert "api_key" not in call.arguments and call.arguments["note"] == "[已隐藏凭据]"


def test_manager_passes_explicit_deadline_budget_to_transport(app: QApplication) -> None:
    transport = FakeTransport()
    manager = ProviderManager(transport, FakeVault(None))
    config = ProviderConfig("test", "Test", "chat", "http://localhost", "fake")
    handle = manager.start(config, ModelRequest("fake", "safety", (ModelMessage("user", "hello"),)), lambda event: None,
                           timeout_ms=179123)
    assert transport.timeout == 179123
    handle.cancel()
