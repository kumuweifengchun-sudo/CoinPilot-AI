"""OKX V5 签名与异步接口。交易和 AI 的网络客户端相互隔离。"""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from .transport import ApiError


def sign(secret, timestamp, method, path, body=b""):
    data = timestamp.encode() + method.encode() + path.encode() + body
    return base64.b64encode(hmac.new(secret.encode(), data, hashlib.sha256).digest()).decode()


class OkxClient:
    def __init__(self, transport, credentials=None, environment="demo", base_url="https://www.okx.com"):
        self.transport = transport
        self.credentials = credentials or {}
        self.environment = environment
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, callback, params=None, data=None, private=False):
        if params:
            path += "?" + urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode() if data is not None else b""
        headers = {}
        if private:
            if not all(self.credentials.get(k) for k in ("key", "secret", "passphrase")):
                callback(None, ApiError("请先在设置中配置该环境的 OKX 凭据"))
                return None
            stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            headers = {"OK-ACCESS-KEY": self.credentials["key"], "OK-ACCESS-TIMESTAMP": stamp,
                       "OK-ACCESS-PASSPHRASE": self.credentials["passphrase"],
                       "OK-ACCESS-SIGN": sign(self.credentials["secret"], stamp, method, path, body)}
            if self.environment == "demo":
                headers["x-simulated-trading"] = "1"

        def finished(result, error):
            if error:
                callback(None, error)
                return
            if str(result.get("code")) != "0":
                code = str(result.get("code", "unknown"))
                # 部分下单错误将错误码放在 data[0].sCode。
                detail = result.get("data") or []
                if detail and isinstance(detail[0], dict):
                    code = str(detail[0].get("sCode") or code)
                callback(None, ApiError(f"OKX 请求未成功（{code}）", code,
                                        method != "GET" and code in ("50004", "50011", "50013")))
            else:
                rows = result.get("data", [])
                candle_request = path.startswith(("/api/v5/market/candles", "/api/v5/market/history-candles"))
                if not isinstance(rows, list) or any(not isinstance(row, list if candle_request else dict) for row in rows):
                    callback(None, ApiError("OKX 返回的数据结构不符合接口要求", uncertain=method != "GET"))
                    return
                callback(rows, None)
        return self.transport.request(method, self.base_url + path, headers, body, finished)

    def get(self, path, callback, params=None, private=False):
        return self.request("GET", path, callback, params=params, private=private)

    def post(self, path, data, callback):
        return self.request("POST", path, callback, data=data, private=True)
