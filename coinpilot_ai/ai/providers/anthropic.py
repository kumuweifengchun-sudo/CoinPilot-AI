from .wire import WireProvider


class AnthropicProvider(WireProvider):
    """Anthropic Messages JSON/SSE，折叠相邻工具结果。"""
