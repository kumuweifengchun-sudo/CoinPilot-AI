from .wire import WireProvider


class OpenAiResponsesProvider(WireProvider):
    """OpenAI Responses JSON/SSE，函数调用使用 call_id。"""
