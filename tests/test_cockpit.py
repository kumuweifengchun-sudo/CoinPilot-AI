import json
import time
from decimal import Decimal

import pytest
from PyQt6.QtTest import QTest

from coinpilot_ai.config import DEFAULT_CONFIG
from coinpilot_ai.cockpit.ai import PromptLibrary, build_request, extract_text, parse_draft, render_prompt, service_url
from coinpilot_ai.cockpit.alerts import AlertEngine, validate_rule
from coinpilot_ai.cockpit.domain import OrderDraft, number
from coinpilot_ai.cockpit.journal import aggregate, summarize
from coinpilot_ai.cockpit.okx import OkxClient, sign
from coinpilot_ai.cockpit.service import CockpitService
from coinpilot_ai.cockpit.store import Store
from coinpilot_ai.cockpit.trading import TradingService
from coinpilot_ai.cockpit.transport import ApiError
from coinpilot_ai.cockpit.workbench import Workbench


class MemoryVault:
    def __init__(self):
        self.values = {}

    def read(self, key):
        return self.values.get(key)

    def write(self, key, value):
        self.values[key] = dict(value)


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "workbench.sqlite3")
    yield result
    result.close()


@pytest.fixture
def service(app, tmp_path):
    config = dict(DEFAULT_CONFIG, proxy_enabled=False)
    result = CockpitService(config, tmp_path / "workbench.sqlite3", tmp_path / "icons", vault=MemoryVault(), autostart=False)
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


SPEC = {"instId": "BTC-USDT-SWAP", "state": "live", "ctType": "linear", "settleCcy": "USDT",
        "lotSz": ".01", "minSz": ".01", "tickSz": ".1", "ctVal": ".01"}


def payload(draft, positions=None, mode="net_mode", **kwargs):
    return draft.payload(SPEC, {"posMode": mode, "time": 100}, positions or [],
                         {"price": "60000", "source": "okx", "time": 100}, kwargs.get("now", 100), "cw123")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "abc"])
def test_nonfinite_amounts_rejected(value):
    with pytest.raises(ValueError):
        number(value)


def test_order_validation_precision_freshness_and_protection():
    draft = OrderDraft("BTCUSDT", size="1.25", stop_loss="59000", take_profit="62000")
    result = payload(draft)
    assert result["side"] == "buy" and result["sz"] == "1.25"
    assert result["attachAlgoOrds"][0]["slOrdPx"] == "-1"
    with pytest.raises(ValueError, match="过期"):
        payload(draft, now=140)
    draft.size = ".015"
    with pytest.raises(ValueError, match="整数倍"):
        payload(draft)
    draft.size, draft.stop_loss = "1", "61000"
    with pytest.raises(ValueError, match="不匹配"):
        payload(draft)


def test_close_never_opens_reverse_position():
    position = {"instId": "BTC-USDT-SWAP", "mgnMode": "cross", "posSide": "net", "pos": "2", "availPos": "1"}
    draft = OrderDraft("BTC-USDT-SWAP", action="close", size="1")
    result = payload(draft, [position])
    assert result["reduceOnly"] is True and result["side"] == "sell"
    draft.size = "2"
    with pytest.raises(ValueError, match="可平"):
        payload(draft, [position])
    draft.action, draft.direction, draft.size = "open", "short", "1"
    with pytest.raises(ValueError, match="反向净仓"):
        payload(draft, [position])


def test_hedge_close_has_position_side():
    position = {"instId": "BTC-USDT-SWAP", "mgnMode": "isolated", "posSide": "short", "pos": "2"}
    result = payload(OrderDraft("BTC-USDT-SWAP", action="close", direction="short", size="1", margin="isolated"), [position], mode="long_short_mode")
    assert result["side"] == "buy" and result["posSide"] == "short"
    assert "reduceOnly" not in result


class FakeApi:
    def __init__(self):
        self.posts, self.gets = [], []

    def post(self, path, data, callback):
        self.posts.append((path, data, callback))

    def get(self, path, callback, params=None, private=False):
        self.gets.append((path, params, callback))


def test_confirmation_consumed_once_and_uncertain_orders_reconciled(store):
    api = FakeApi()
    service = TradingService(api, store, "demo:test", lambda d, c: dict(payload(d), clOrdId=c), lambda _: None)
    draft = OrderDraft("BTC-USDT-SWAP", size="1")
    token, request = service.prepare(draft, {"reason": "test"})
    with pytest.raises(ValueError, match="确认"):
        service.submit(token)
    key = service.submit(token, confirmed=True)
    assert len(api.posts) == 1
    with pytest.raises(ValueError):
        service.submit(token, confirmed=True)
    api.posts[0][2](None, ApiError("timeout", uncertain=True))
    assert store.get("local_order", key, scope="demo:test")["status"] == "unknown"
    api.gets[0][2](None, ApiError("not found", "51603"))
    assert store.get("local_order", key, scope="demo:test")["status"] == "unknown"
    with pytest.raises(ValueError, match="待确认"):
        service.prepare(draft, {})
    recovered = TradingService(api, store, "demo:test", lambda d, c: dict(payload(d), clOrdId=c), lambda _: None)
    recovered.recover()
    api.gets[-1][2]([{"ordId": "1", "state": "filled", "instId": draft.instrument}], None)
    assert store.get("local_order", key, scope="demo:test")["status"] == "filled"
    assert len(api.posts) == 1


def test_live_gate_and_modified_drafts_cannot_bypass_confirmation(store):
    service = TradingService(FakeApi(), store, "live:x", lambda *_: {}, lambda _: None, allowed=lambda: False)
    with pytest.raises(ValueError, match="未解锁"):
        service.prepare(OrderDraft("BTC-USDT-SWAP", size="1"), {})


def rule(metric="price", threshold="100", cooldown=300):
    return validate_rule({"name": "test", "instrument": "BTCUSDT", "mode": "all", "cooldown": cooldown,
                          "conditions": [{"metric": metric, "op": "above", "threshold": threshold, "window": 60}]})


def test_rules_baseline_cooldown_rearm_and_stale_gap():
    engine, r = AlertEngine(), rule()
    def run(price, now, age=0):
        return engine.evaluate("a", r, {"source": "okx", "price": str(price), "time": now-age}, [], {}, now)
    assert run(110, 1000) is None  # 启动时不把已成立条件当作新穿越。
    assert run(90, 1001) is None
    assert run(101, 1002)["evidence"][0]["value"] == "101"
    assert run(102, 1500) is None
    assert run(90, 1501) is None
    assert run(101, 1502)
    assert run(90, 1503) is None
    assert run(105, 1504) is None  # 冷却内抑制，持续成立不延迟补报。
    assert run(106, 2000) is None
    assert run(90, 2001, age=40) is None
    assert run(110, 2002) is None


def test_change_window_needs_continuous_observations():
    engine, r = AlertEngine(), rule("change_pct", "1", 0)
    for now in range(1000, 1061):
        engine.tick("BTC-USDT-SWAP", "100", now)
    q = {"source": "okx", "price": "100", "time": 1060}
    assert engine.evaluate("a", r, q, [], {}, 1060) is None
    q.update(price="102", time=1061)
    engine.tick("BTC-USDT-SWAP", "102", 1061)
    assert engine.evaluate("a", r, q, [], {}, 1061)
    engine.tick("BTC-USDT-SWAP", "110", 1200)
    q.update(price="110", time=1200)
    assert engine.evaluate("a", r, q, [], {}, 1200) is None


def fill(identity, size, side, pnl="0", fee="-1", pos_side="net", stamp=None, **extra):
    return dict({"billId": str(identity), "ordId": str(identity), "instId": "BTC-USDT-SWAP", "fillTime": str(stamp or identity*1000),
                 "ts": str(stamp or identity*1000), "side": side, "posSide": pos_side, "fillSz": size, "fillPx": "60000",
                 "fillPnl": pnl, "fee": fee, "feeCcy": "USDT", "mgnMode": "cross"}, **extra)


def test_journal_add_partial_close_and_reverse_fee_split():
    fills = [fill(1, "2", "buy"), fill(2, "1", "buy"), fill(3, "1", "sell", pnl="10"),
             fill(4, "3", "sell", pnl="20", fee="-3"), fill(5, "1", "buy", pnl="-5")]
    trades, unknown = aggregate(fills + [fills[0]])
    assert len(trades) == 2 and not unknown
    assert trades[0]["direction"] == "long" and trades[0]["quantity"] == 0
    assert trades[0]["realized"] == 30 and trades[0]["fees"] == -5
    assert trades[1]["direction"] == "short" and trades[1]["realized"] == -5
    assert trades[1]["fees"] == -2
    assert Decimal(summarize(trades)["known_net"]) == Decimal("18")


def test_journal_incomplete_history_and_ambiguous_funding():
    trades, _ = aggregate([fill(1, "2", "sell", pos_side="long")])
    assert trades[0]["status"] == "incomplete"
    fills = [fill(1, "2", "buy", pos_side="long"), fill(2, "2", "sell", pos_side="short")]
    bills = [{"billId": "b", "type": "8", "instId": "BTC-USDT-SWAP", "ts": "3000", "balChg": "-2", "ccy": "USDT"}]
    trades, unknown = aggregate(fills, bills)
    assert len(unknown) == 1 and all(t["funding"] == 0 for t in trades)
    bills[0]["posSide"] = "long"
    trades, unknown = aggregate(fills, bills)
    assert not unknown and trades[0]["funding"] == -2


@pytest.mark.parametrize("protocol,suffix", [("responses", "/responses"), ("messages", "/messages"), ("chat", "/chat/completions")])
def test_ai_protocol_requests(protocol, suffix):
    config = {"protocol": protocol, "base_url": "https://example.com/v1", "model": "my-model"}
    url, headers, body = build_request(config, "test-secret", "system", "user")
    assert url == "https://example.com/v1" + suffix
    data = json.loads(body)
    assert data["model"] == "my-model" and "test-secret" not in body.decode()
    assert "tools" not in data
    if protocol == "messages":
        assert headers["x-api-key"] == "test-secret" and data["max_tokens"] > 0
    elif protocol == "responses":
        assert data["store"] is False and data["input"] == "user"
    else:
        assert data["messages"][1]["content"] == "user"


def test_ai_parsing_and_prompt_versions(store):
    assert extract_text("responses", {"output": [{"type": "message", "content": [{"type": "output_text", "text": "r"}]}]}) == "r"
    assert extract_text("messages", {"content": [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": "m"}]}) == "m"
    assert extract_text("chat", {"choices": [{"message": {"content": "c"}}]}) == "c"
    with pytest.raises(ValueError):
        extract_text("chat", {"error": {"message": "secret"}})
    with pytest.raises(ValueError):
        service_url("https://key:secret@example.com/v1", "chat")
    library = PromptLibrary(store)
    key, original = library.templates("review")[0]
    store.append("report", {"template": original}, "demo")
    library.save("review", original["name"], "自定义：{{trades}}", key)
    assert store.get("template", key)["version"] == 2
    assert store.list("report", "demo")[0][1]["template"]["version"] == 1
    assert "完整事实资料" in render_prompt("按我自己的方式分析", {"trades": [1]})
    assert parse_draft('分析\n```json\n{"instrument":"BTCUSDT"}\n```')["instrument"] == "BTCUSDT"


def test_scopes_and_store_persistence(store):
    store.put("order", "1", {"value": 1}, "demo:a")
    store.put("order", "1", {"value": 2}, "live:a")
    assert store.get("order", "1", scope="demo:a")["value"] == 1
    assert store.get("order", "1", scope="live:a")["value"] == 2
    assert store.get("order", "1", scope="demo:b") is None


def test_okx_signature_uses_exact_body_and_simulation_header():
    class Transport:
        def request(self, method, url, headers, body, callback):
            self.request_data = method, url, headers, body
    transport = Transport()
    api = OkxClient(transport, {"key": "k", "secret": "s", "passphrase": "p"})
    api.post("/api/v5/trade/order", {"instId": "BTC-USDT-SWAP", "sz": "1"}, lambda *_: None)
    method, url, headers, body = transport.request_data
    assert headers["x-simulated-trading"] == "1"
    assert headers["OK-ACCESS-SIGN"] == sign("s", headers["OK-ACCESS-TIMESTAMP"], method, "/api/v5/trade/order", body)


def test_workbench_closing_keeps_service_and_mini_compatibility(service, app):
    window = Workbench(service, lambda: None)
    window.show()
    app.processEvents()
    assert window.pages.count() == 4
    assert service.settings["watchlist"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    window.close()
    assert not window.isVisible() and not service.closed
    window.exiting = True
    window.show()
    assert window.close() is True
    assert not service.trading_allowed() if service.environment == "live" else service.trading_allowed()
    window.deleteLater()
    app.processEvents()


def test_real_trading_requires_all_demo_checks(service):
    service.environment = "live"
    assert not service.trading_allowed()
    service.environment = "demo"
    for check in ("open", "close", "cancel", "protection", "recovery"):
        service.mark_demo(check, {"time": 100})
    service.environment = "live"
    assert service.trading_allowed()


def test_cockpit_rejects_buffered_old_quotes_without_changing_mini_feed(service):
    now = time.time()
    service.market.exchange_times["BTCUSDT"] = now - 60
    service._price("BTCUSDT", Decimal("60000"), "", "okx")
    assert "BTC-USDT-SWAP" not in service.quotes
    service.market.exchange_times["BTCUSDT"] = now
    service._price("BTCUSDT", Decimal("61000"), "", "okx")
    assert service.quotes["BTC-USDT-SWAP"]["time"] == now
    service.market.exchange_times["BTCUSDT"] = now - 1
    service._price("BTCUSDT", Decimal("60000"), "", "okx")
    assert service.quotes["BTC-USDT-SWAP"]["price"] == "61000"


def test_simulated_broker_full_order_protection_cancel_recovery_flow(service):
    class Broker:
        credentials = {"key": "test"}

        def __init__(self):
            self.orders, self.positions, self.algos = {}, [], []
            self.offline = False

        def get(self, path, callback, params=None, private=False):
            if self.offline:
                callback(None, ApiError("offline", "0"))
                return
            if path.endswith("/config"):
                data = [{"posMode": "net_mode"}]
            elif path.endswith("/balance"):
                data = [{"totalEq": "1000"}]
            elif path.endswith("/positions"):
                data = self.positions
            elif path.endswith("/orders-pending"):
                data = [o for o in self.orders.values() if o["state"] == "live"]
            elif path.endswith("/orders-algo-pending"):
                data = self.algos
            elif path.endswith("/order"):
                data = [self.orders[params["clOrdId"]]]
            else:
                data = []
            callback(data, None)

        def post(self, path, data, callback):
            if path.endswith("/order"):
                identity = str(len(self.orders) + 1)
                row = dict(data, ordId=identity, state="live" if data["ordType"] == "limit" else "filled", uTime=str(int(time.time()*1000)))
                self.orders[data["clOrdId"]] = row
                if data.get("reduceOnly"):
                    self.positions = []
                elif row["state"] == "filled":
                    self.positions = [{"instId": data["instId"], "posId": "p1", "posSide": "net", "pos": data["sz"], "mgnMode": "cross"}]
                callback([{"ordId": identity, "sCode": "0"}], None)
            elif path.endswith("/cancel-order"):
                for order in self.orders.values():
                    if order["ordId"] == data["ordId"]:
                        order.update(state="canceled", uTime=str(int(time.time()*1000)))
                callback([{"sCode": "0"}], None)
            elif path.endswith("/order-algo"):
                self.algos = [dict(data, algoId="algo1", state="live")]
                callback([{"algoId": "algo1", "sCode": "0"}], None)
    broker = Broker()
    service.api = broker
    service.trading.api = broker
    service.specs = {SPEC["instId"]: SPEC}
    service.quotes[SPEC["instId"]] = {"price": "60000", "source": "okx", "time": time.time()}
    service.refresh_account()
    def submit(draft):
        token, _ = service.trading.prepare(draft, {})
        key = service.trading.submit(token, confirmed=True)
        service.refresh_account()
        return key
    submit(OrderDraft(SPEC["instId"], size="1"))
    assert service.store.get("demo_check", "open")
    service.trade_action("/api/v5/trade/order-algo", {"instId": SPEC["instId"], "slTriggerPx": "59000", "tpTriggerPx": "62000"}, lambda *_: None)
    assert service.store.get("demo_check", "protection")
    submit(OrderDraft(SPEC["instId"], action="close", size="1"))
    assert service.store.get("demo_check", "close")
    key = submit(OrderDraft(SPEC["instId"], order_type="limit", price="59000", size="1"))
    order_id = service.store.get("local_order", key, scope=service.scope)["order_id"]
    service.trade_action("/api/v5/trade/cancel-order", {"instId": SPEC["instId"], "ordId": order_id}, lambda *_: None)
    service.trading.recover()
    assert service.store.get("demo_check", "cancel")
    broker.offline = True
    service.refresh_account()
    assert service.account["time"] == 0
    broker.offline = False
    service.refresh_account()
    assert service.store.get("demo_check", "recovery")
    service.environment = "live"
    assert service.trading_allowed()


def test_unknown_protection_request_blocks_duplicate_until_reconciled(service):
    broker = FakeApi()
    broker.credentials = {"key": "test"}
    service.api = broker
    service.account = {"time": time.time()}
    service.refresh_account = lambda: None
    request = {"instId": "BTC-USDT-SWAP", "algoClOrdId": "testalgo", "ordType": "oco", "sz": "1"}
    service.trade_action("/api/v5/trade/order-algo", request, lambda *_: None)
    broker.posts[0][2](None, ApiError("timeout", uncertain=True))
    with pytest.raises(ValueError, match="待确认"):
        service.trade_action("/api/v5/trade/order-algo", request, lambda *_: None)
    assert len(broker.posts) == 1 and broker.gets[-1][1] == {"algoClOrdId": "testalgo"}
    broker.gets[-1][2]([{"algoId": "1", "state": "live"}], None)
    assert service.store.list("action", service.scope)[0][1]["status"] == "confirmed"


def test_review_only_sends_unassigned_costs_relevant_to_selected_trades(service):
    row = fill(1, "1", "buy", fee="-.01", feeCcy="BTC")
    service.store.put("fills", "1", row, service.scope)
    service.store.put("fills", "2", fill(2, "1", "sell"), service.scope)
    other = dict(fill(3, "1", "buy", fee="-.02", feeCcy="BTC"), instId="ETH-USDT-SWAP")
    service.store.put("fills", "3", other, service.scope)
    trades, _ = service.journal()
    selected = next(t["id"] for t in trades if t["instrument"] == "BTC-USDT-SWAP")
    context = service.review_context([selected], 0, time.time())
    assert len(context["unassigned_costs"]) == 1
    assert context["unassigned_costs"][0]["trade"] == selected
