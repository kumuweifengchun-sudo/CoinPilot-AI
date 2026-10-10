from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlparse

from PyQt6.QtCore import QTimer

from .anthropic import AnthropicProvider
from .base import AiProvider, ModelRequest, ProviderCallback, ProviderConfig, ProviderFailed, StreamTransport
from .chat_completions import OpenAiChatProvider
from .responses import OpenAiResponsesProvider
from ..transport import RequestHandle


class CredentialReader(Protocol):
    def read(self, key: str) -> object: ...


class ProviderManager:
    def __init__(self, transport: StreamTransport, vault: CredentialReader) -> None:
        self.transport, self.vault = transport, vault

    def provider(self, config: ProviderConfig) -> AiProvider:
        try:
            credentials = self.vault.read("ai/" + config.id)
        except OSError:
            raise ValueError("无法读取 AI 服务凭据") from None
        if credentials is None:
            key = ""
        elif isinstance(credentials, dict) and isinstance(credentials.get("key"), str):
            key = credentials["key"]
        else:
            raise ValueError("AI 服务凭据格式无效")
        if not key and urlparse(config.base_url).hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("尚未保存该 AI 服务的密钥")
        providers = {"responses": OpenAiResponsesProvider, "chat": OpenAiChatProvider, "messages": AnthropicProvider}
        return providers[config.protocol](config, key, self.transport)

    def start(self, config: ProviderConfig, request: ModelRequest, callback: ProviderCallback, *, timeout_ms: int = 90000) -> RequestHandle:
        try:
            return self.provider(config).start(request, callback, timeout_ms=timeout_ms)
        except (ValueError, OSError, KeyError):
            # 不传播第三方异常正文；配置错误和凭据错误都用固定公开消息。
            active = [True]
            def deliver() -> None:
                if active[0]:
                    active[0] = False
                    callback(ProviderFailed("AI 服务配置或凭据无效，请检查设置"))
            def cancel() -> None:
                if active[0]:
                    active[0] = False
                    callback(ProviderFailed("AI 请求已取消", cancelled=True))
            QTimer.singleShot(0, deliver)
            return RequestHandle("configuration-error", cancel)
