import json
from dataclasses import replace
from collections.abc import Callable

import pytest

from coinpilot_ai.ai.models import JsonValue
from coinpilot_ai.ai.providers.base import (
    ModelMessage, ModelRequest, ProviderConfig, ProviderEvent, ProviderProtocol,
    ProviderCompleted, ProviderFailed, TextDelta, ToolCall, ToolRequested,
    ChatTokenField,
)
from coinpilot_ai.ai.providers.wire import WireSession, build_body
from coinpilot_ai.ai.transport import StreamFrame


def config(protocol: ProviderProtocol) -> ProviderConfig:
    return ProviderConfig("fake", "Fake", protocol, "http://localhost/v1", "fake-model")


def deliver(session: WireSession, row: dict[str, JsonValue]) -> None:
    session.frame(StreamFrame(json.dumps(row)))


@pytest.mark.parametrize("protocol", ["responses", "chat", "messages"])
def test_all_protocols_encode_multi_turn_tools_without_credentials(protocol: ProviderProtocol) -> None:
    call = ToolCall("call", "get_market_snapshot", {"instrument": "BTC-USDT-SWAP"})
    request = ModelRequest("fake-model", "immutable safety", (
        ModelMessage("user", "问题"), ModelMessage("assistant", "检查中", (call,)),
        ModelMessage("tool", "结果一", tool_call_id="call"),
        ModelMessage("tool", "结果二", tool_call_id="other"), ModelMessage("user", "追问"),
    ), ({"name": "get_market_snapshot", "description": "market", "parameters": {"type": "object"}},))
    body = build_body(config(protocol), request)
    encoded = json.dumps(body, ensure_ascii=False)
    assert "get_market_snapshot" in encoded and "call" in encoded and "追问" in encoded
    assert "Authorization" not in encoded and "api_key" not in encoded
    if protocol == "responses":
        assert body["store"] is False and "function_call_output" in encoded
    elif protocol == "messages":
        assert "input_schema" in encoded and "tool_result" in encoded
        messages = body["messages"]
        assert isinstance(messages, list) and len(messages) == 3


@pytest.mark.parametrize("protocol", ["responses", "chat", "messages"])
def test_streamed_text_and_tools_and_usage(protocol: ProviderProtocol) -> None:
    events: list[ProviderEvent] = []
    session = WireSession(config(protocol), events.append)
    if protocol == "responses":
        rows: list[dict[str, JsonValue]] = [
            {"type": "response.output_text.delta", "delta": "你好"},
            {"type": "response.output_item.added", "item": {"type": "function_call", "id": "item", "call_id": "call", "name": "get_candles", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "item", "delta": '{"limit":'},
            {"type": "response.function_call_arguments.delta", "item_id": "item", "delta": "300}"},
            {"type": "response.completed", "response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 20}}},
        ]
    elif protocol == "chat":
        rows = [
            {"choices": [{"index": 0, "delta": {"content": "你好"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "function": {"name": "get_candles", "arguments": '{"limit":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "300}"}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20}},
        ]
    else:
        rows = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "call", "name": "get_candles", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"limit":300}'}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 20}},
            {"type": "message_stop"},
        ]
    for row in rows:
        deliver(session, row)
    assert not any(isinstance(event, ToolRequested) for event in events)
    session.done(None, None)
    assert events == [TextDelta("你好"), ToolRequested(ToolCall("call", "get_candles", {"limit": 300})), ProviderCompleted(10, 20)]


@pytest.mark.parametrize("protocol,response", [
    ("responses", {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "答案"}]}]}),
    ("chat", {"choices": [{"finish_reason": "stop", "message": {"content": "答案"}}]}),
    ("messages", {"stop_reason": "end_turn", "content": [{"type": "text", "text": "答案"}]}),
])
def test_json_fallback(protocol: ProviderProtocol, response: dict[str, JsonValue]) -> None:
    events: list[ProviderEvent] = []
    WireSession(config(protocol), events.append).done(response, None)
    assert events == [TextDelta("答案"), ProviderCompleted()]


@pytest.mark.parametrize("protocol", ["responses", "chat", "messages"])
def test_malformed_error_and_incomplete_stream_are_sanitized(protocol: ProviderProtocol) -> None:
    for frame in (StreamFrame("echoed-secret"), StreamFrame('{"error":{"message":"echoed-secret"}}')):
        events: list[ProviderEvent] = []
        session = WireSession(config(protocol), events.append)
        session.frame(frame)
        session.done(None, None)
        assert len(events) == 1 and isinstance(events[0], ProviderFailed)
        assert "echoed-secret" not in events[0].error
    events = []
    WireSession(config(protocol), events.append).done(None, None)
    assert len(events) == 1 and isinstance(events[0], ProviderFailed)


def test_invalid_tool_arguments_prevent_all_tool_publication() -> None:
    events: list[ProviderEvent] = []
    session = WireSession(config("chat"), events.append)
    session.done({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "first", "function": {"name": "get_candles", "arguments": "{}"}},
        {"id": "second", "function": {"name": "get_candles", "arguments": "broken-private-data"}},
    ]}}]}, None)
    assert len(events) == 1 and isinstance(events[0], ProviderFailed)
    assert "private-data" not in events[0].error


def test_hidden_reasoning_is_never_published() -> None:
    events: list[ProviderEvent] = []
    session = WireSession(config("responses"), events.append)
    deliver(session, {"type": "response.output_item.added", "item": {"type": "reasoning", "text": "private"}})
    deliver(session, {"type": "response.completed", "response": {"status": "completed", "output": [
        {"type": "reasoning", "summary": [{"text": "private"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "公开答案"}]},
    ]}})
    session.done(None, None)
    assert events == [TextDelta("公开答案"), ProviderCompleted()]


def test_provider_permissions_default_closed_and_record_fields_validated() -> None:
    parsed = ProviderConfig.from_record({"id": "test", "protocol": "chat", "base_url": "http://localhost", "model": "fake"})
    assert not parsed.allow_account and not parsed.allow_history
    with pytest.raises(ValueError):
        ProviderConfig.from_record({"id": "test", "protocol": "chat", "base_url": "http://localhost", "model": "fake", "allow_account": "true"})


@pytest.mark.parametrize("token_field", ["max_tokens", "max_completion_tokens", "omit"])
@pytest.mark.parametrize("usage", [True, False])
def test_chat_endpoint_capabilities_control_token_field_and_stream_usage(token_field: ChatTokenField, usage: bool) -> None:
    selected = replace(config("chat"), chat_token_field=token_field, stream_usage=usage)
    request = ModelRequest("any-model-name", "safety", (ModelMessage("user", "问题"),), max_output_tokens=1234)
    body = build_body(selected, request)
    assert ("stream_options" in body) == usage
    if token_field == "omit":
        assert "max_tokens" not in body and "max_completion_tokens" not in body
    else:
        assert body[token_field] == 1234
        assert ("max_tokens" in body) != ("max_completion_tokens" in body)
    assert "stream_options" not in build_body(selected, replace(request, stream=False))


@pytest.mark.parametrize("extra", [{"chat_token_field": "guess-from-model"}, {"stream_usage": "yes"}])
def test_invalid_endpoint_capabilities_are_rejected(extra: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        ProviderConfig.from_record({"id": "test", "protocol": "chat", "base_url": "http://localhost", "model": "fake", **extra})
