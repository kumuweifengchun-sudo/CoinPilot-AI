"""聊天持久化契约。标识与状态不可原地改写，外部数据先校验再构造。"""

from dataclasses import dataclass, field
import math
from typing import Literal, TypeGuard

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type TurnStatus = Literal["running", "complete", "failed", "cancelled", "interrupted"]
type TerminalStatus = Literal["complete", "failed", "cancelled", "interrupted"]
type ItemKind = Literal[
    "user_message", "assistant_message", "tool_call", "tool_result", "order_draft",
    "context_notice", "error", "status", "indicator_draft",
]
type PinKind = Literal["chart", "position", "trade", "event", "report"]


def is_turn_status(value: object) -> TypeGuard[TurnStatus]:
    return isinstance(value, str) and value in (
        "running", "complete", "failed", "cancelled", "interrupted",
    )


def is_terminal_status(value: object) -> TypeGuard[TerminalStatus]:
    return is_turn_status(value) and value != "running"


def is_item_kind(value: object) -> TypeGuard[ItemKind]:
    return isinstance(value, str) and value in (
        "user_message", "assistant_message", "tool_call", "tool_result", "order_draft",
        "context_notice", "error", "status", "indicator_draft",
    )


def validate_text(value: object, name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{name}必须是{'非空' if nonempty else ''}字符串")
    return value


def validate_integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name}必须是大于等于 {minimum} 的整数")
    return value


def validate_timestamp(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}必须是有限的非负时间戳")
    try:
        stamp = float(value)
    except OverflowError:
        raise ValueError(f"{name}必须是有限的非负时间戳") from None
    if not math.isfinite(stamp) or stamp < 0:
        raise ValueError(f"{name}必须是有限的非负时间戳")
    return stamp


def _json_value(value: object, depth: int) -> JsonValue:
    if depth > 32:
        raise ValueError("JSON 嵌套超过 32 层")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON 数值必须有限")
        return value
    if isinstance(value, list):
        return [_json_value(item, depth + 1) for item in value]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON 对象键必须是字符串")
            result[key] = _json_value(item, depth + 1)
        return result
    raise ValueError("内容必须是 JSON 数据")


def validate_payload(value: object) -> dict[str, JsonValue]:
    clean = _json_value(value, 0)
    if not isinstance(clean, dict):
        raise ValueError("payload 必须是 JSON 对象")
    return clean


@dataclass(frozen=True)
class Conversation:
    id: str
    scope: str
    title: str
    provider_id: str
    model: str
    summary: str
    summary_until_turn: int
    created_at: float
    updated_at: float
    archived: bool = False
    summary_account: bool = False
    summary_history: bool = False

    def __post_init__(self) -> None:
        validate_text(self.id, "会话标识", nonempty=True)
        validate_text(self.scope, "scope", nonempty=True)
        validate_text(self.title, "标题", nonempty=True)
        validate_text(self.provider_id, "服务标识")
        validate_text(self.model, "模型")
        validate_text(self.summary, "摘要")
        validate_integer(self.summary_until_turn, "摘要轮次")
        validate_timestamp(self.created_at, "创建时间")
        validate_timestamp(self.updated_at, "更新时间")
        if self.updated_at < self.created_at or not isinstance(self.archived, bool):
            raise ValueError("无效的会话更新时间或归档状态")
        if not isinstance(self.summary_account, bool) or not isinstance(self.summary_history, bool):
            raise ValueError("无效的摘要权限来源")


@dataclass(frozen=True)
class Turn:
    id: str
    conversation_id: str
    sequence: int
    status: TurnStatus
    provider_id: str
    model: str
    started_at: float
    completed_at: float | None = None
    error: str = ""

    def __post_init__(self) -> None:
        validate_text(self.id, "轮次标识", nonempty=True)
        validate_text(self.conversation_id, "会话标识", nonempty=True)
        validate_integer(self.sequence, "轮次顺序", minimum=1)
        if not is_turn_status(self.status):
            raise ValueError("无效的轮次状态")
        validate_text(self.provider_id, "服务标识")
        validate_text(self.model, "模型")
        validate_text(self.error, "错误说明")
        validate_timestamp(self.started_at, "开始时间")
        if self.completed_at is not None:
            validate_timestamp(self.completed_at, "结束时间")
            if self.completed_at < self.started_at:
                raise ValueError("结束时间早于开始时间")
        if (self.status == "running") != (self.completed_at is None):
            raise ValueError("轮次状态与结束时间不一致")


@dataclass(frozen=True)
class Item:
    id: str
    turn_id: str
    sequence: int
    kind: ItemKind
    status: TurnStatus
    text: str
    created_at: float
    updated_at: float
    payload: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_text(self.id, "条目标识", nonempty=True)
        validate_text(self.turn_id, "轮次标识", nonempty=True)
        validate_integer(self.sequence, "条目顺序", minimum=1)
        if not is_item_kind(self.kind) or not is_turn_status(self.status):
            raise ValueError("无效的条目种类或状态")
        validate_text(self.text, "条目文本")
        validate_timestamp(self.created_at, "创建时间")
        validate_timestamp(self.updated_at, "更新时间")
        if self.updated_at < self.created_at:
            raise ValueError("更新时间早于创建时间")
        # 深复制隔离调用者的嵌套集合；持久化时仍会再次验证。
        object.__setattr__(self, "payload", validate_payload(self.payload))


@dataclass(frozen=True)
class ContextPin:
    id: str
    conversation_id: str
    sequence: int
    kind: PinKind
    reference: str
    label: str

    def __post_init__(self) -> None:
        for name in ("id", "conversation_id", "reference", "label"):
            validate_text(getattr(self, name), name, nonempty=True)
        validate_integer(self.sequence, "固定上下文顺序", minimum=1)
        if self.kind not in ("chart", "position", "trade", "event", "report"):
            raise ValueError("无效的固定上下文类型")
