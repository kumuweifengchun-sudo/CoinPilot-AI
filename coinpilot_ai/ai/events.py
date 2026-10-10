"""Provider 无关的内部事件；不承载模型私有推理或传输凭据。"""

from dataclasses import dataclass, field
from typing import Literal, TypeGuard

from .models import JsonValue, validate_payload, validate_text

type AiEventKind = Literal[
    "turn.started", "item.started", "item.delta", "item.completed", "tool.started",
    "tool.completed", "order_draft.created", "turn.completed", "turn.failed", "turn.cancelled",
]


def is_event_kind(value: object) -> TypeGuard[AiEventKind]:
    return isinstance(value, str) and value in (
        "turn.started", "item.started", "item.delta", "item.completed", "tool.started",
        "tool.completed", "order_draft.created", "turn.completed", "turn.failed", "turn.cancelled",
    )


@dataclass(frozen=True)
class AiEvent:
    kind: AiEventKind
    conversation_id: str
    turn_id: str
    item_id: str | None = None
    payload: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not is_event_kind(self.kind):
            raise ValueError("无效的 AI 事件类型")
        validate_text(self.conversation_id, "会话标识", nonempty=True)
        validate_text(self.turn_id, "轮次标识", nonempty=True)
        if self.item_id is not None:
            validate_text(self.item_id, "条目标识", nonempty=True)
        if not self.kind.startswith("turn.") and self.item_id is None:
            raise ValueError("条目事件必须携带条目标识")
        object.__setattr__(self, "payload", validate_payload(self.payload))
