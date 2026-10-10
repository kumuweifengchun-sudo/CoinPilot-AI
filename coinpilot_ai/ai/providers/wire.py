"""三种 wire 协议的共同生命周期与严格解码；只输出可公开文本和工具调用。"""

from dataclasses import dataclass, field
import json

from ..models import JsonValue, validate_integer, validate_payload, validate_text
from ..privacy import scrub_text, scrub_value
from ..transport import RequestHandle, StreamError, StreamFrame
from .base import (
    ModelMessage, ModelRequest, ProviderCallback, ProviderCompleted, ProviderConfig, ProviderEvent, ProviderFailed,
    StreamTransport, TextDelta, ToolCall, ToolRequested, service_url,
)


def json_text(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def object_value(value: object) -> dict[str, JsonValue]:
    return validate_payload(value)


def array_value(value: object) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError("协议数组缺失")
    # 通过统一边界验证递归结构，再收窄。
    clean = validate_payload({"array": value})["array"]
    if not isinstance(clean, list):
        raise ValueError("协议数组无效")
    return clean


def string_value(value: object, *, nonempty: bool = False) -> str:
    return validate_text(value, "协议文本", nonempty=nonempty)


@dataclass
class _CallBuffer:
    id: str = ""
    name: str = ""
    arguments: str = ""


def _chat_message(message: ModelMessage) -> dict[str, JsonValue]:
    row: dict[str, JsonValue] = {"role": message.role, "content": message.text}
    if message.tool_calls:
        row["tool_calls"] = [{"id": call.id, "type": "function", "function": {
            "name": call.name, "arguments": json_text(call.arguments),
        }} for call in message.tool_calls]
    if message.role == "tool":
        row["tool_call_id"] = message.tool_call_id
    return row


def build_body(config: ProviderConfig, request: ModelRequest) -> dict[str, JsonValue]:
    common: dict[str, JsonValue] = {"model": request.model, "stream": request.stream}
    if config.protocol == "responses":
        inputs: list[JsonValue] = []
        for message in request.messages:
            if message.role == "tool":
                inputs.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": message.text})
                continue
            if message.text:
                inputs.append({"role": message.role, "content": message.text})
            for call in message.tool_calls:
                inputs.append({"type": "function_call", "call_id": call.id, "name": call.name,
                               "arguments": json_text(call.arguments)})
        common.update(instructions=request.system, input=inputs, store=False,
                      max_output_tokens=request.max_output_tokens)
        if request.tools:
            common["tools"] = [{"type": "function", **tool} for tool in request.tools]
    elif config.protocol == "chat":
        chat_messages: list[JsonValue] = [{"role": "system", "content": request.system}]
        chat_messages.extend(_chat_message(message) for message in request.messages)
        common["messages"] = chat_messages
        # 能力由用户明确配置；不猜测模型名，不改变参数后自动重试 POST。
        if config.chat_token_field != "omit":
            common[config.chat_token_field] = request.max_output_tokens
        if request.stream and config.stream_usage:
            common["stream_options"] = {"include_usage": True}
        if request.tools:
            common["tools"] = [{"type": "function", "function": tool} for tool in request.tools]
    else:
        messages: list[JsonValue] = []
        for message in request.messages:
            role = "user" if message.role == "tool" else message.role
            blocks: list[JsonValue] = []
            if message.role == "tool":
                blocks.append({"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.text})
            else:
                if message.text:
                    blocks.append({"type": "text", "text": message.text})
                blocks.extend({"type": "tool_use", "id": call.id, "name": call.name,
                               "input": call.arguments} for call in message.tool_calls)
            # Anthropic 同一轮多个工具结果必须在同一个 user message 中。
            previous = messages[-1] if messages else None
            if isinstance(previous, dict) and previous.get("role") == role:
                content = previous.get("content")
                if not isinstance(content, list):
                    raise ValueError("消息内容无效")
                content.extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
        common.update(system=request.system, messages=messages, max_tokens=request.max_output_tokens)
        if request.tools:
            common["tools"] = [{"name": tool["name"], "description": tool["description"],
                                "input_schema": tool["parameters"]} for tool in request.tools]
    return common


class WireSession:
    def __init__(self, config: ProviderConfig, callback: ProviderCallback) -> None:
        self.config = config
        self.callback = callback
        self.handle: RequestHandle | None = None
        self.ended = False
        self.failed = False
        self.saw_done = False
        self.saw_reason = False
        self.saw_text = False
        self.calls: dict[str, _CallBuffer] = {}
        self.input_tokens = 0
        self.output_tokens = 0

    def fail(self, message: str, *, cancelled: bool = False) -> None:
        if not self.ended:
            self.ended = True
            self.failed = True
            self.callback(ProviderFailed(message, cancelled))
            if self.handle is not None:
                self.handle.cancel()

    def _text(self, value: object) -> None:
        text = string_value(value)
        if text and not self.ended:
            self.saw_text = True
            self.callback(TextDelta(text))

    def _buffer(self, index: str) -> _CallBuffer:
        if index not in self.calls:
            if len(self.calls) >= 20:
                raise ValueError("过多工具调用")
            self.calls[index] = _CallBuffer()
        return self.calls[index]

    def frame(self, frame: StreamFrame) -> None:
        if self.ended:
            return
        try:
            if self.config.protocol == "chat" and frame.data == "[DONE]":
                # [DONE] 不能将未结束 choice 的截断响应伪装成成功。
                if not self.saw_done:
                    raise ValueError("缺少完成原因")
                return
            row = object_value(json.loads(frame.data))
            if row.get("error") or frame.event == "error":
                raise ValueError("Provider 错误")
            if self.config.protocol == "responses":
                self._responses(row)
            elif self.config.protocol == "chat":
                self._chat(row)
            else:
                self._anthropic(row)
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            self.fail("AI 返回了无效或未完成的协议数据")

    def done(self, result: dict[str, JsonValue] | None, error: StreamError | None) -> None:
        if self.ended:
            return
        if error is not None:
            self.fail(error.message, cancelled=error.cancelled)
            return
        try:
            if result is not None:
                self._json(result)
            if not self.saw_done or not (self.saw_text or self.calls):
                raise ValueError("未完成或无可显示输出")
            calls: list[ToolCall] = []
            for buffer in self.calls.values():
                arguments = object_value(json.loads(buffer.arguments or "{}"))
                calls.append(ToolCall(buffer.id, buffer.name, arguments))
            # 校验全部参数后再发布，避免后面的 malformed JSON 导致前面工具提前执行。
            self.ended = True
            for call in calls:
                self.callback(ToolRequested(call))
            self.callback(ProviderCompleted(self.input_tokens, self.output_tokens))
        except (ValueError, TypeError, KeyError, IndexError, RecursionError):
            self.fail("AI 返回了无效或未完成的协议数据")

    def _usage(self, value: object) -> None:
        if value is None:
            return
        usage = object_value(value)
        self.input_tokens = validate_integer(usage.get("input_tokens", usage.get("prompt_tokens", self.input_tokens)), "输入 token")
        self.output_tokens = validate_integer(usage.get("output_tokens", usage.get("completion_tokens", self.output_tokens)), "输出 token")

    def _response_item(self, value: object, *, include_text: bool = False) -> None:
        item = object_value(value)
        if item.get("type") == "function_call":
            identifier = string_value(item.get("call_id"), nonempty=True)
            buffer = self._buffer(string_value(item.get("id", identifier), nonempty=True))
            buffer.id = identifier
            buffer.name = string_value(item.get("name"), nonempty=True)
            if "arguments" in item:
                buffer.arguments = string_value(item["arguments"])
        elif include_text and item.get("type") == "message":
            for value in array_value(item.get("content")):
                part = object_value(value)
                if part.get("type") == "output_text":
                    self._text(part.get("text"))
                elif part.get("type") == "refusal":
                    self._text(part.get("refusal"))

    def _responses(self, row: dict[str, JsonValue]) -> None:
        kind = row.get("type")
        if kind == "response.output_text.delta":
            self._text(row.get("delta"))
        elif kind == "response.refusal.delta":
            self._text(row.get("delta"))
        elif kind in ("response.output_item.added", "response.output_item.done"):
            self._response_item(row.get("item"))
        elif kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
            buffer = self._buffer(string_value(row.get("item_id"), nonempty=True))
            if kind.endswith("delta"):
                buffer.arguments += string_value(row.get("delta"))
            else:
                buffer.arguments = string_value(row.get("arguments"))
        elif kind == "response.completed":
            response = object_value(row.get("response"))
            if response.get("status") != "completed":
                raise ValueError("响应未完成")
            self.saw_done = True
            self._usage(response.get("usage"))
            include_text = not self.saw_text
            for value in array_value(response.get("output", [])):
                self._response_item(value, include_text=include_text)
        elif kind in ("response.failed", "response.incomplete", "error"):
            raise ValueError("响应未完成")

    def _chat(self, row: dict[str, JsonValue]) -> None:
        self._usage(row.get("usage"))
        choices = array_value(row.get("choices"))
        for value in choices:
            choice = object_value(value)
            if choice.get("index", 0) != 0:
                continue
            delta = object_value(choice.get("delta", {}))
            if delta.get("content") is not None:
                self._text(delta["content"])
            if delta.get("refusal") is not None:
                self._text(delta["refusal"])
            for call_value in array_value(delta.get("tool_calls", [])):
                part = object_value(call_value)
                index = validate_integer(part.get("index"), "工具索引")
                buffer = self._buffer(str(index))
                if "id" in part:
                    buffer.id = string_value(part["id"], nonempty=True)
                function = object_value(part.get("function", {}))
                if "name" in function:
                    buffer.name += string_value(function["name"])
                if "arguments" in function:
                    buffer.arguments += string_value(function["arguments"])
            reason = choice.get("finish_reason")
            if reason in ("stop", "tool_calls"):
                self.saw_done = True
            elif reason is not None:
                raise ValueError("输出未完成")

    def _anthropic(self, row: dict[str, JsonValue]) -> None:
        kind = row.get("type")
        if kind == "message_start":
            self._usage(object_value(row.get("message")).get("usage"))
        elif kind == "content_block_start":
            part = object_value(row.get("content_block"))
            if part.get("type") == "tool_use":
                buffer = self._buffer(str(validate_integer(row.get("index"), "工具索引")))
                buffer.id = string_value(part.get("id"), nonempty=True)
                buffer.name = string_value(part.get("name"), nonempty=True)
                initial = object_value(part.get("input", {}))
                buffer.arguments = json_text(initial) if initial else ""
            elif part.get("type") == "text":
                self._text(part.get("text", ""))
        elif kind == "content_block_delta":
            delta = object_value(row.get("delta"))
            if delta.get("type") == "text_delta":
                self._text(delta.get("text"))
            elif delta.get("type") == "input_json_delta":
                buffer = self._buffer(str(validate_integer(row.get("index"), "工具索引")))
                buffer.arguments += string_value(delta.get("partial_json"))
        elif kind == "message_delta":
            delta = object_value(row.get("delta"))
            if delta.get("stop_reason") in ("end_turn", "tool_use", "stop_sequence"):
                self.saw_reason = True
            elif delta.get("stop_reason") is not None:
                raise ValueError("输出未完成")
            self._usage(row.get("usage"))
        elif kind == "message_stop":
            if not self.saw_reason:
                raise ValueError("缺少结束原因")
            self.saw_done = True
        elif kind == "error":
            raise ValueError("Provider 错误")

    def _json(self, row: dict[str, JsonValue]) -> None:
        if row.get("error"):
            raise ValueError("Provider 错误")
        if self.config.protocol == "responses":
            if row.get("status") != "completed":
                raise ValueError("响应未完成")
            for value in array_value(row.get("output", [])):
                self._response_item(value, include_text=True)
            if not self.saw_text and row.get("output_text") is not None:
                self._text(row["output_text"])
            self._usage(row.get("usage"))
        elif self.config.protocol == "chat":
            choices = array_value(row.get("choices"))
            choice = object_value(choices[0])
            if choice.get("finish_reason") not in ("stop", "tool_calls"):
                raise ValueError("输出未完成")
            message = object_value(choice.get("message"))
            if message.get("content") is not None:
                self._text(message["content"])
            if message.get("refusal") is not None:
                self._text(message["refusal"])
            for index, value in enumerate(array_value(message.get("tool_calls", []))):
                call = object_value(value)
                function = object_value(call.get("function"))
                buffer = self._buffer(str(index))
                buffer.id, buffer.name = string_value(call.get("id"), nonempty=True), string_value(function.get("name"), nonempty=True)
                buffer.arguments = string_value(function.get("arguments"))
            self._usage(row.get("usage"))
        else:
            if row.get("stop_reason") not in ("end_turn", "tool_use", "stop_sequence"):
                raise ValueError("输出未完成")
            for index, value in enumerate(array_value(row.get("content"))):
                part = object_value(value)
                if part.get("type") == "text":
                    self._text(part.get("text"))
                elif part.get("type") == "tool_use":
                    buffer = self._buffer(str(index))
                    buffer.id, buffer.name = string_value(part.get("id"), nonempty=True), string_value(part.get("name"), nonempty=True)
                    buffer.arguments = json_text(object_value(part.get("input")))
            self._usage(row.get("usage"))
        self.saw_done = True


class WireProvider:
    def __init__(self, config: ProviderConfig, key: str, transport: StreamTransport) -> None:
        self.config, self._key, self.transport = config, key, transport

    def start(self, request: ModelRequest, callback: ProviderCallback, *, timeout_ms: int = 90000) -> RequestHandle:
        validate_integer(timeout_ms, "模型请求超时", minimum=1)
        pending = ""
        def publish(event: ProviderEvent) -> None:
            nonlocal pending
            if event.kind == "text_delta":
                pending = scrub_text(pending + event.text, (self._key,))
                reserved = 0
                if self._key:
                    for length in range(1, min(len(self._key), len(pending) + 1)):
                        if pending.endswith(self._key[:length]):
                            reserved = length
                safe = pending[:-reserved] if reserved else pending
                pending = pending[-reserved:] if reserved else ""
                if safe:
                    callback(TextDelta(safe))
            elif event.kind == "tool_call":
                arguments = scrub_value(event.call.arguments, (self._key,))
                callback(ToolRequested(ToolCall(scrub_text(event.call.id, (self._key,)),
                                                scrub_text(event.call.name, (self._key,)), object_value(arguments))))
            else:
                if pending:
                    callback(TextDelta("[已隐藏凭据]" if self._key.startswith(pending) else pending))
                    pending = ""
                callback(event)
        session = WireSession(self.config, publish)
        headers = ({"x-api-key": self._key, "anthropic-version": "2023-06-01"}
                   if self.config.protocol == "messages" else {"Authorization": "Bearer " + self._key})
        handle = self.transport.post(service_url(self.config.base_url, self.config.protocol), headers,
                                     build_body(self.config, request), session.frame, session.done, timeout=timeout_ms)
        session.handle = handle
        if session.failed:
            handle.cancel()
        return handle
