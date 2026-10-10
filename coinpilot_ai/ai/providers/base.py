"""模型请求与公开输出的统一契约，不传递模型私有推理。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlparse

from ..models import JsonValue, validate_integer, validate_payload, validate_text
from ..transport import FrameCallback, RequestHandle, StreamCallback

type ProviderProtocol = Literal["responses", "chat", "messages"]
type ChatTokenField = Literal["max_tokens", "max_completion_tokens", "omit"]


def chat_token_field(value: object) -> ChatTokenField:
    if value == "max_tokens":
        return "max_tokens"
    if value == "max_completion_tokens":
        return "max_completion_tokens"
    if value == "omit":
        return "omit"
    raise ValueError("无效的 Chat token 上限参数")


def service_url(base: str, protocol: ProviderProtocol) -> str:
    base = base.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("接口地址不能包含凭据、查询参数或片段")
    if not parsed.hostname or not (parsed.scheme == "https" or
            parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("AI 地址需要 HTTPS，本机服务可使用 HTTP")
    paths = {"responses": "responses", "chat": "chat/completions", "messages": "messages"}
    if protocol not in paths:
        raise ValueError("不支持的 AI 协议")
    suffix = paths[protocol]
    if base.endswith("/" + suffix):
        return base
    return base + ("/" if parsed.path.rstrip("/") else "/v1/") + suffix


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    name: str
    protocol: ProviderProtocol
    base_url: str
    model: str
    context_window: int = 32768
    stream: bool = True
    tools: bool = True
    allow_account: bool = False
    allow_history: bool = False
    max_output_tokens: int = 4096
    chat_token_field: ChatTokenField = "max_tokens"
    stream_usage: bool = True

    def __post_init__(self) -> None:
        for name in ("id", "name", "model"):
            validate_text(getattr(self, name), name, nonempty=True)
        service_url(self.base_url, self.protocol)
        validate_integer(self.context_window, "上下文窗口", minimum=4096)
        validate_integer(self.max_output_tokens, "输出上限", minimum=1)
        if self.context_window > 2000000 or self.max_output_tokens >= self.context_window:
            raise ValueError("无效的上下文窗口或输出上限")
        chat_token_field(self.chat_token_field)
        for name in ("stream", "tools", "allow_account", "allow_history", "stream_usage"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError("AI 权限和能力设置必须是布尔值")

    @classmethod
    def from_record(cls, value: object) -> "ProviderConfig":
        row = validate_payload(value)
        protocol = row.get("protocol")
        if protocol not in ("responses", "chat", "messages"):
            raise ValueError("不支持的 AI 协议")
        if protocol == "responses":
            selected: ProviderProtocol = "responses"
        elif protocol == "chat":
            selected = "chat"
        else:
            selected = "messages"
        options: dict[str, bool] = {}
        for key, default in (("stream", True), ("tools", True), ("allow_account", False), ("allow_history", False), ("stream_usage", True)):
            option = row.get(key, default)
            if not isinstance(option, bool):
                raise ValueError("无效的 AI 能力或权限设置")
            options[key] = option
        return cls(
            id=validate_text(row.get("id"), "服务标识", nonempty=True),
            name=validate_text(row.get("name", "AI"), "服务名称", nonempty=True), protocol=selected,
            base_url=validate_text(row.get("base_url"), "接口地址", nonempty=True),
            model=validate_text(row.get("chat_model") or row.get("model"), "模型", nonempty=True),
            context_window=validate_integer(row.get("context_window", 32768), "上下文窗口", minimum=4096),
            max_output_tokens=validate_integer(row.get("max_output_tokens", 4096), "输出上限", minimum=1),
            chat_token_field=chat_token_field(row.get("chat_token_field", "max_tokens")),
            **options,
        )


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, JsonValue]

    def __post_init__(self) -> None:
        validate_text(self.id, "工具调用标识", nonempty=True)
        validate_text(self.name, "工具名", nonempty=True)
        object.__setattr__(self, "arguments", validate_payload(self.arguments))


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[dict[str, JsonValue], ...] = ()
    stream: bool = True
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class TextDelta:
    text: str
    kind: Literal["text_delta"] = field(default="text_delta", init=False)


@dataclass(frozen=True)
class ToolRequested:
    call: ToolCall
    kind: Literal["tool_call"] = field(default="tool_call", init=False)


@dataclass(frozen=True)
class ProviderCompleted:
    input_tokens: int = 0
    output_tokens: int = 0
    kind: Literal["completed"] = field(default="completed", init=False)


@dataclass(frozen=True)
class ProviderFailed:
    error: str
    cancelled: bool = False
    kind: Literal["failed"] = field(default="failed", init=False)


type ProviderEvent = TextDelta | ToolRequested | ProviderCompleted | ProviderFailed
type ProviderCallback = Callable[[ProviderEvent], None]


class StreamTransport(Protocol):
    def post(self, url: str, headers: dict[str, str], body: dict[str, JsonValue],
             on_frame: FrameCallback, on_done: StreamCallback, *, timeout: int = 90000) -> RequestHandle: ...


class AiProvider(Protocol):
    def start(self, request: ModelRequest, callback: ProviderCallback, *, timeout_ms: int = 90000) -> RequestHandle: ...
