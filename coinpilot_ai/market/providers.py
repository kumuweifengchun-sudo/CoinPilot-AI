"""三个公开永续合约行情接口的交易对映射与响应校验。"""

import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from coinpilot_ai.core.config import normalize_symbol

SOURCE_ORDER = ("binance", "okx", "bybit")
SOURCE_NAMES = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}


def split_symbol(symbol):
    for quote in ("FDUSD", "USDT", "USDC", "TUSD", "BUSD", "DAI", "BTC", "ETH", "BNB", "EUR", "TRY", "USD", "BRL", "AUD", "GBP"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[:-len(quote)], quote
    return symbol, ""


def instrument_id(source, symbol):
    symbol = normalize_symbol(symbol)
    if source == "okx":
        base, quote = split_symbol(symbol)
        if not quote:
            raise ValueError("无法识别计价币种，请使用 BTCUSDT 等完整合约交易对")
        return f"{base}-{quote}-SWAP"
    return symbol


def price_url(source, symbol):
    instrument = instrument_id(source, symbol)
    if source == "binance":
        return "https://fapi.binance.com/fapi/v1/ticker/price?" + urlencode({"symbol": instrument})
    if source == "okx":
        return "https://www.okx.com/api/v5/market/ticker?" + urlencode({"instId": instrument})
    if source == "bybit":
        return "https://api.bybit.com/v5/market/tickers?" + urlencode({"category": "linear", "symbol": instrument})
    raise ValueError("不支持的行情数据源")


def _positive_price(raw):
    value = Decimal(str(raw))
    if not value.is_finite() or value <= 0 or value.adjusted() > 20:
        raise ValueError("价格无效")
    return value


def parse_price(data):
    """兼容原有 Binance 价格解析接口。"""
    try:
        return _positive_price(json.loads(data)["price"])
    except (ValueError, TypeError, KeyError, InvalidOperation, UnicodeError) as exc:
        raise ValueError("行情响应无效") from exc


def parse_provider_price(source, symbol, data):
    try:
        payload = json.loads(data)
        instrument = instrument_id(source, symbol)
        if source == "binance":
            if payload.get("symbol") != instrument:
                raise ValueError("交易对不匹配")
            raw = payload["price"]
        elif source == "okx":
            if payload.get("code") != "0":
                raise ValueError("交易所返回错误")
            item = next(row for row in payload["data"] if row["instId"] == instrument)
            if item.get("instType") != "SWAP":
                raise ValueError("不是永续合约行情")
            raw = item["last"]
        elif source == "bybit":
            if payload.get("retCode") != 0 or payload["result"]["category"] != "linear":
                raise ValueError("不是有效的线性合约行情")
            item = next(row for row in payload["result"]["list"] if row["symbol"] == instrument)
            raw = item["lastPrice"]
        else:
            raise ValueError("不支持的数据源")
        return _positive_price(raw)
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, InvalidOperation, UnicodeError) as exc:
        raise ValueError("交易对不受支持或合约行情响应无效") from exc


def stream_subscription(source, symbol):
    instrument = instrument_id(source, symbol)
    if source == "binance":
        return f"wss://fstream.binance.com/market/ws/{instrument.lower()}@ticker", None
    if source == "okx":
        return "wss://ws.okx.com:8443/ws/v5/public", {
            "op": "subscribe", "args": [{"channel": "tickers", "instId": instrument}]}
    if source == "bybit":
        return "wss://stream.bybit.com/v5/public/linear", {
            "op": "subscribe", "args": [f"tickers.{instrument}"]}
    raise ValueError("不支持的数据源")


def parse_stream_price(source, symbol, data, previous=None):
    """忽略心跳、订阅确认和其他交易对；Bybit 增量沿用最近成交价。"""
    if data == "pong":
        return None
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("无效消息")
        if payload.get("event") == "error" or payload.get("success") is False:
            raise ValueError("订阅失败")
        if "code" in payload and str(payload["code"]) != "0":
            raise ValueError("交易所返回错误")
        instrument = instrument_id(source, symbol)
        if source == "binance":
            if payload.get("e") != "24hrTicker" or payload.get("s") != instrument:
                return None
            return _positive_price(payload["c"])
        if source == "okx":
            arg = payload.get("arg", {})
            if (payload.get("event") or arg.get("channel") != "tickers"
                    or arg.get("instId") != instrument):
                return None
            for item in payload["data"]:
                if item.get("instId") == instrument and item.get("instType") == "SWAP":
                    return _positive_price(item["last"])
            return None
        if source == "bybit":
            if payload.get("topic") != f"tickers.{instrument}":
                return None
            item = payload["data"]
            if item.get("symbol") != instrument:
                return None
            if "lastPrice" in item:
                return _positive_price(item["lastPrice"])
            return previous if payload.get("type") == "delta" else None
        raise ValueError("不支持的数据源")
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, UnicodeError) as exc:
        raise ValueError("WebSocket 行情消息无效") from exc
