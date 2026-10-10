"""旧事件解读、复盘和连接测试继续使用原接口，共用新的 ProviderManager。"""

from collections.abc import Callable
from dataclasses import replace
import uuid

from PyQt6.QtCore import QTimer

from coinpilot_ai.integrations.transport import ApiError
from coinpilot_ai.review.ai import DRAFT_REQUEST
from .prompts import CORE_SYSTEM_INSTRUCTIONS
from .providers.base import ModelMessage, ModelRequest, ProviderConfig, ProviderEvent, ProviderFailed
from .providers.manager import ProviderManager
from .transport import RequestHandle

type LegacyCallback = Callable[[str | None, ApiError | None], None]


class LegacyAiAdapter:
    def __init__(self, manager: ProviderManager) -> None:
        self.manager = manager
        self._pending: dict[str, RequestHandle] = {}

    def ask(self, config: object, prompt: str, callback: LegacyCallback, draft: bool = False) -> str:
        identifier = uuid.uuid4().hex
        parts: list[str] = []
        ended = False
        def receive(event: ProviderEvent) -> None:
            nonlocal ended
            if ended:
                return
            if event.kind == "text_delta":
                parts.append(event.text)
            elif event.kind in ("completed", "failed", "tool_call"):
                ended = True
                self._pending.pop(identifier, None)
                if event.kind == "completed":
                    callback("".join(parts), None)
                elif event.kind == "failed":
                    callback(None, ApiError(event.error, cancelled=event.cancelled))
                else:
                    callback(None, ApiError("旧分析请求不允许调用工具"))
        try:
            parsed = ProviderConfig.from_record(config)
            # 聊天默认模型与旧场景模型分开；旧报告继续记录并使用 config.model。
            if isinstance(config, dict) and isinstance(config.get("model"), str):
                parsed = replace(parsed, model=config["model"])
            parsed = replace(parsed, stream=False, tools=False)
            request = ModelRequest(parsed.model, CORE_SYSTEM_INSTRUCTIONS + ("\n" + DRAFT_REQUEST if draft else ""),
                                   (ModelMessage("user", prompt),), stream=False, max_output_tokens=parsed.max_output_tokens)
            handle = self.manager.start(parsed, request, receive)
            if not ended:
                self._pending[identifier] = handle
        except (ValueError, OSError, KeyError):
            self._pending[identifier] = RequestHandle(identifier, lambda: receive(ProviderFailed("AI 请求已取消", cancelled=True)))
            QTimer.singleShot(0, lambda: receive(ProviderFailed("AI 服务配置无效，请检查设置")))
        return identifier

    def cancel(self, identifier: str) -> None:
        handle = self._pending.pop(identifier, None)
        if handle is not None:
            handle.cancel()

    def cancel_all(self) -> None:
        for identifier in list(self._pending):
            self.cancel(identifier)

    def close(self) -> None:
        self.cancel_all()
