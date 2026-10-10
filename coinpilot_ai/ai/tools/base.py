from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Literal

from ..models import JsonValue, validate_payload, validate_timestamp
from ..privacy import scrub_value
from ..transport import RequestHandle

type ToolPermission = Literal["market", "account", "history", "order_draft"]
type Freshness = Literal["live", "stale", "historical", "replay", "missing"]


@dataclass(frozen=True)
class ToolContext:
    scope: str
    environment: str
    captured_at: float
    allow_account: bool = False
    allow_history: bool = False


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    captured_at: float
    source: str
    freshness: Freshness
    warnings: tuple[str, ...]
    data: dict[str, JsonValue]

    def payload(self) -> dict[str, JsonValue]:
        validate_timestamp(self.captured_at, "工具时间")
        clean = scrub_value(validate_payload(self.data))
        if not isinstance(clean, dict):
            raise ValueError("工具结果必须是对象")
        return {"ok": self.ok, "captured_at": self.captured_at, "source": self.source,
                "freshness": self.freshness, "warnings": list(self.warnings), "data": clean}

    @classmethod
    def failure(cls, message: str) -> "ToolResult":
        return cls(False, time.time(), "coinpilot", "missing", (message,), {})


type ToolCallback = Callable[[ToolResult], None]
type ToolHandler = Callable[[ToolContext, dict[str, JsonValue], ToolCallback], RequestHandle]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, JsonValue]
    permission: ToolPermission
    handler: ToolHandler
