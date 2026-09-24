import json
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from coinpilot_ai.config import validate_config
from coinpilot_ai.providers import parse_provider_price, price_url


PAYLOADS = {
    "binance": {"symbol": "BTCUSDT", "price": "123.456"},
    "okx": {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "last": "123.456"}]},
    "bybit": {"retCode": 0, "result": {"category": "linear", "list": [{"symbol": "BTCUSDT", "lastPrice": "123.456"}]}},
}


@pytest.mark.parametrize("source,query", [
    ("binance", {"symbol": ["BTCUSDT"]}),
    ("okx", {"instId": ["BTC-USDT-SWAP"]}),
    ("bybit", {"category": ["linear"], "symbol": ["BTCUSDT"]}),
])
def test_perpetual_request_mapping(source, query):
    assert parse_qs(urlparse(price_url(source, "BTCUSDT")).query) == query
    assert parse_provider_price(source, "BTCUSDT", json.dumps(PAYLOADS[source])) == Decimal("123.456")


@pytest.mark.parametrize("source", list(PAYLOADS))
def test_wrong_instrument_and_broken_payload_rejected(source):
    with pytest.raises(ValueError):
        parse_provider_price(source, "ETHUSDT", json.dumps(PAYLOADS[source]))
    for raw in ("null", "[]", "{}", "bad", "{\"data\": []}"):
        with pytest.raises(ValueError):
            parse_provider_price(source, "BTCUSDT", raw)


@pytest.mark.parametrize("source,payload", [
    ("okx", {"code": "51001", "data": []}),
    ("okx", {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "instType": "SPOT", "last": "1"}]}),
    ("okx", {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "last": "NaN"}]}),
    ("bybit", {"retCode": 10001, "result": {"category": "linear", "list": []}}),
    ("bybit", {"retCode": 0, "result": {"category": "inverse", "list": [{"symbol": "BTCUSDT", "lastPrice": "1"}]}}),
    ("bybit", {"retCode": 0, "result": {"category": "linear", "list": [{"symbol": "BTCUSDT", "lastPrice": "0"}]}}),
])
def test_api_errors_derivatives_and_bad_prices_rejected(source, payload):
    with pytest.raises(ValueError):
        parse_provider_price(source, "BTCUSDT", json.dumps(payload))


def test_unknown_quote_is_not_guessed_for_okx():
    with pytest.raises(ValueError, match="计价币种"):
        price_url("okx", "UNKNOWNPAIR")
    assert parse_qs(urlparse(price_url("okx", "ETHBTC")).query) == {"instId": ["ETH-BTC-SWAP"]}


def test_source_config_migration_and_bad_values():
    assert validate_config({})[0]["price_source"] == "auto"
    assert validate_config({"price_source": "okx"})[0]["price_source"] == "okx"
    assert validate_config({"price_source": []})[1] == ["price_source"]
