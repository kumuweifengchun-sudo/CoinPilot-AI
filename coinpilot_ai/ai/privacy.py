"""仅处理已知凭据和敏感字段，不猜测普通行情字符串。"""

from .models import JsonValue

_SENSITIVE = {"apikey", "key", "secret", "apisecret", "passphrase", "authorization", "xapikey", "password"}


def scrub_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[已隐藏凭据]")
    return text


def scrub_value(value: JsonValue, secrets: tuple[str, ...] = ()) -> JsonValue:
    if isinstance(value, str):
        return scrub_text(value, secrets)
    if isinstance(value, list):
        return [scrub_value(part, secrets) for part in value]
    if isinstance(value, dict):
        return {key: scrub_value(part, secrets) for key, part in value.items()
                if key.lower().replace("_", "").replace("-", "") not in _SENSITIVE}
    return value
