from dataclasses import replace

import pytest

from coinpilot_ai.ai.events import AiEvent, AiEventKind
from coinpilot_ai.ai.models import Conversation, Item, JsonValue, Turn, validate_payload


@pytest.mark.parametrize("changes", [
    {"id": ""}, {"scope": " "}, {"title": ""}, {"summary_until_turn": -1},
    {"summary_until_turn": True}, {"created_at": float("nan")}, {"updated_at": 0}, {"archived": 1},
])
def test_conversation_rejects_invalid_fields(changes: dict[str, object]) -> None:
    conversation = Conversation("conversation", "demo:test", "标题", "", "", "", 0, 1, 1)
    with pytest.raises(ValueError):
        replace(conversation, **changes)


@pytest.mark.parametrize("changes", [
    {"sequence": 0}, {"sequence": True}, {"status": "unknown"}, {"completed_at": 1},
    {"status": "complete"}, {"status": "failed", "completed_at": 0}, {"started_at": float("inf")},
])
def test_turn_rejects_invalid_state_and_time(changes: dict[str, object]) -> None:
    turn = Turn("turn", "conversation", 1, "running", "", "", 1)
    with pytest.raises(ValueError):
        replace(turn, **changes)


@pytest.mark.parametrize("payload", [
    [], None, {"price": float("nan")}, {"price": float("inf")}, {1: "non-string-key"},
    {"object": object()}, {"tuple": (1, 2)},
])
def test_payload_rejects_non_json_values(payload: object) -> None:
    with pytest.raises(ValueError):
        validate_payload(payload)


def test_payload_depth_is_bounded_even_for_cycles() -> None:
    cyclic: dict[str, JsonValue] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="嵌套"):
        validate_payload(cyclic)


def test_payload_is_detached_from_mutable_input() -> None:
    values: list[JsonValue] = ["original"]
    payload: dict[str, JsonValue] = {"values": values}
    item = Item("item", "turn", 1, "tool_result", "complete", "", 1, 1, payload)
    event = AiEvent("tool.completed", "conversation", "turn", "item", payload)
    values.append("changed")
    assert item.payload == event.payload == {"values": ["original"]}


@pytest.mark.parametrize("changes", [
    {"kind": "reasoning"}, {"kind": "chain_of_thought"}, {"status": "unknown"},
    {"sequence": 0}, {"updated_at": 0},
])
def test_item_rejects_unsupported_kinds_and_invalid_fields(changes: dict[str, object]) -> None:
    item = Item("item", "turn", 1, "assistant_message", "running", "", 1, 1)
    with pytest.raises(ValueError):
        replace(item, **changes)


@pytest.mark.parametrize("kind", [
    "turn.started", "item.started", "item.delta", "item.completed", "tool.started",
    "tool.completed", "order_draft.created", "turn.completed", "turn.failed", "turn.cancelled",
])
def test_internal_events_are_provider_independent(kind: AiEventKind) -> None:
    event = AiEvent(kind, "conversation", "turn", "item", {"text": "可展示文本"})
    assert event.payload == {"text": "可展示文本"}


@pytest.mark.parametrize("changes", [
    {"kind": "provider.raw"}, {"conversation_id": ""}, {"turn_id": ""},
    {"item_id": ""}, {"kind": "item.delta", "item_id": None},
])
def test_events_validate_identifiers_and_kind(changes: dict[str, object]) -> None:
    event = AiEvent("turn.started", "conversation", "turn")
    with pytest.raises(ValueError):
        replace(event, **changes)
