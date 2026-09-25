"""由应用持有的共享后台服务，不依赖任何工作台窗口。"""
import hashlib
import math
import sqlite3
import time
import uuid
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from coinpilot_ai.market.client import MarketClient
from coinpilot_ai.review.ai import AiClient, PromptLibrary, render_prompt
from coinpilot_ai.trading.alerts import AlertEngine, RuleState, validate_rule
from coinpilot_ai.market.derivatives import DerivativeMonitor
from coinpilot_ai.trading.models import instrument_id, number, aligned
from coinpilot_ai.trading.history import HistorySync
from coinpilot_ai.review.journal import aggregate, summarize
from coinpilot_ai.integrations.okx import OkxClient
from coinpilot_ai.trading.paper import PaperBroker
from coinpilot_ai.core.credentials import CredentialVault
from coinpilot_ai.core.store import Store, encode
from coinpilot_ai.trading.service import TradingService
from coinpilot_ai.integrations.transport import ApiError, JsonTransport
from coinpilot_ai.charts.state import ChartBook
from coinpilot_ai.market.candles import ChartFeed

DEMO_CHECKS = {"open": "开仓成交", "close": "平仓成交", "cancel": "撤单完成",
               "protection": "止盈止损已生成", "recovery": "断线后账户与订单恢复"}


class CockpitService(QObject):
    updated = pyqtSignal(str)
    event_created = pyqtSignal(str, object)
    message = pyqtSignal(str)
    ai_finished = pyqtSignal(str, object)

    def __init__(self, config, data_path, cache_dir, parent=None, *, vault=None, autostart=True):
        super().__init__(parent)
        self.config = dict(config)
        self.store = Store(data_path)
        self.chart_book = ChartBook(self.store, self)
        self.running = False
        # 凭据标识保持兼容；品牌更名不应要求用户重新录入现有密钥。
        namespace = "CryptoWidget/" + hashlib.sha256(str(Path(data_path).resolve()).encode()).hexdigest()[:16]
        self.vault = vault or CredentialVault(namespace)
        self.prompts = PromptLibrary(self.store)
        watchlist = []
        for i in range(1, 4):
            try:
                watchlist.append(instrument_id(config[f"symbol{i}"]))
            except ValueError:
                pass
        self.settings = self.store.get("settings", "workbench", {
            "environment": "demo", "watchlist": list(dict.fromkeys(watchlist)) or ["BTC-USDT-SWAP"],
            "notifications_paused": False, "sound": False, "auto_explain": True, "scenes": {}})
        self.environment = self.settings["environment"]
        self.selected = self.settings["watchlist"][0] if self.settings["watchlist"] else "BTC-USDT-SWAP"
        self.bar = "15m"
        self.chart_pairs = set()
        self.indicator_pairs = set()
        self.quotes, self.candles, self.candle_times, self.specs = {}, {}, {}, {}
        self.positions, self.pending_orders, self.algos = [], [], []
        self.account, self.balance = {}, {}
        self.leverages = {}
        self.account_error = "尚未连接账户"
        self.market_error = "行情连接中"
        self.closed = False
        self.started_at = time.time()
        self.generation = 0
        self.account_busy = False
        self.had_account_failure = False
        self.awaiting_recovery = False
        self.last_heartbeat = time.monotonic()
        self.ai_tasks = {}
        self.ai_queue = []
        self.actions_reconciling = set()
        self.last_history = 0
        self.last_account = 0
        self.last_candle_poll = 0
        self.last_spec_poll = 0
        self.last_derivative_poll = 0
        self.last_reconcile = 0
        self.engine = AlertEngine()
        self.transport = JsonTransport(self)
        self.ai_transport = JsonTransport(self)
        self.transport.set_proxy(config)
        self.ai_transport.set_proxy(config)
        self.ai = AiClient(self.ai_transport, self.vault)
        self.market = MarketClient(cache_dir, parent=self, source="okx")
        self.market.set_proxy(config)
        self.market.price_ready.connect(self._price)
        self.chart_feed = ChartFeed(self)
        self.derivatives = DerivativeMonitor(self)
        self.derivatives.changed.connect(self._evaluate)
        self._configure_account()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        if autostart:
            self.start()

    def _configure_account(self):
        try:
            credentials = {} if self.environment == 'paper' else self.vault.read("okx/" + self.environment) or {}
        except OSError as exc:
            credentials = {}
            self.account_error = str(exc)
        fingerprint = hashlib.sha256(credentials.get("key", "unconfigured").encode()).hexdigest()[:16]
        self.scope = self.environment + ":" + fingerprint
        self.api = OkxClient(self.transport, credentials, self.environment)
        if self.environment == 'paper':
            self.scope = PaperBroker.SCOPE
            self.api = PaperBroker(self.store, self.api, lambda: self.specs, lambda: self.quotes,
                                   self._paper_changed, fee=self.settings.get("paper_fee", "0.0005"),
                                   slippage_bps=self.settings.get("paper_slippage_bps", "0"))
        self.trading = TradingService(self.api, self.store, self.scope, self.validate_order,
                                      self._local_order_changed, self.trading_allowed)
        self.history = HistorySync(self.api, self.store, self.scope, self._history_row, self._history_finished)
        for key, state in self.store.list("rule_state", self.scope):
            self.engine.states[key] = RuleState(last_fired=state.get("last_fired", 0),
                                                 last_bar=state.get("last_bar", ""),
                                                 fired_once=state.get("fired_once", False))
        if self.environment == 'paper':
            self.refresh_account()

    @property
    def account_connected(self):
        return self.environment == 'paper' or bool(self.api.credentials)

    @property
    def environment_label(self):
        return {'paper': '本地模拟', 'demo': '模拟环境', 'live': '真实环境'}[self.environment]

    def _paper_changed(self):
        if not self.closed and self.environment == 'paper':
            self.refresh_account()
            changes, self.api.order_changes = self.api.order_changes, []
            for order in changes:
                self._observe_order(order)
            self.trading.recover()
            self.updated.emit('orders')
            self.updated.emit('history')

    def start(self):
        self.running = True
        self.chart_feed.activate()
        self.timer.start()
        self.refresh_market()
        self._tick()

    def _instruments(self, rows, error):
        if self.closed:
            return
        if error:
            self.market_error = str(error)
        else:
            self.specs = {row["instId"]: row for row in rows if row.get("settleCcy") == "USDT" and row.get("ctType") == "linear"}
        self.updated.emit("market")

    def instruments(self):
        result = set(self.settings["watchlist"]) | {self.selected}
        result.update(pair[0] for pair in self.chart_pairs)
        result.update(p["instId"] for p in self.positions if number(p.get("pos") or "0"))
        result.update(o['instId'] for o in self.pending_orders)
        result.update(r["instrument"] for _, r in self.rules() if r.get("enabled", True))
        return sorted(result)

    def refresh_market(self):
        self.market.refresh_prices([inst.replace("-USDT-SWAP", "USDT") for inst in self.instruments()])

    def _price(self, symbol, price, error, source):
        if self.closed:
            return
        inst = instrument_id(symbol)
        server_time = self.market.exchange_times.get(symbol)
        if (error or source != "okx" or server_time is None or not math.isfinite(server_time)
                or not -5 <= time.time() - server_time <= 30):
            self.market_error = f"{inst} 行情不可用"
            self.quotes.pop(inst, None)
            self.engine.invalidate(inst)
        else:
            now = time.time()
            previous = self.quotes.get(inst, {})
            if server_time < previous.get("time", 0):
                return
            self.quotes[inst] = {"instrument": inst, "price": str(price), "source": "okx", "time": server_time, "received_at": now}
            self.engine.tick(inst, price, server_time)
            self.market_error = ""
            if self.environment == 'paper':
                try:
                    self.api.match(inst)
                    self.refresh_account()
                except (ValueError, OSError, sqlite3.Error) as exc:
                    message = f'本地模拟成交未完成：{exc}'
                    if self.account_error != message:
                        self.message.emit(message)
                    self.account_error = message
                    self.account['time'] = 0
                    self.updated.emit('account')
            self._evaluate(inst)
        self.updated.emit("price")

    def _tick(self):
        if self.closed:
            return
        mono, now = time.monotonic(), time.time()
        if mono - self.last_heartbeat > 30:
            self.engine.invalidate()
            self.quotes.clear()
            self.account["time"] = 0
            self.candle_times.clear()
            self.market_error = "监控存在休眠或暂停缺口，正在重新同步"
            self.chart_feed.reset()
            self.chart_feed.activate()
            self.last_candle_poll = 0
        self.last_heartbeat = mono
        if now - self.last_spec_poll >= (300 if self.specs else 30):
            self.last_spec_poll = now
            self.api.get("/api/v5/public/instruments", self._instruments, {"instType": "SWAP"})
        if now - self.last_derivative_poll >= 60:
            self.last_derivative_poll = now
            wanted = {self.selected}
            wanted.update(inst for inst, _ in self.chart_pairs)
            wanted.update(rule["instrument"] for _, rule in self.rules() if rule.get("enabled", True)
                          and any(c.get("metric") in ("funding", "oi", "oi_change_pct", "mark", "index", "basis")
                                  for c in rule.get("conditions", [])))
            self.derivatives.schedule(wanted)
        if now - self.last_account >= 8:
            self.last_account = now
            self.refresh_account()
            self.refresh_market()
        if now - self.last_candle_poll >= (20 if self.chart_feed.stream_state == "实时" else 5):
            self.last_candle_poll = now
            pairs = {(self.selected, self.bar)} | self.chart_pairs | self.indicator_pairs
            for _, rule in self.rules():
                if rule.get("enabled", True):
                    pairs.update((rule["instrument"], c.get("bar", "15m")) for c in rule.get("conditions", [])
                                 if c["metric"] in ("volume_ratio", "indicator"))
                    if rule.get("trigger") == "bar":
                        pairs.add((rule["instrument"], rule.get("trigger_bar", "1m")))
            for i, (inst, bar) in enumerate(sorted(pairs)):
                QTimer.singleShot(i * 350, lambda a=inst, b=bar: self.fetch_candles(a, b))
        if now - self.last_reconcile >= 10 and self.account_connected:
            self.last_reconcile = now
            self.trading.recover()
            self.recover_actions()
        if self.api.credentials and now - self.last_history >= 60 and not self.history.running:
            self.last_history = now
            cursor = self.store.get("state", "history_cursor", now - 30 * 86400, self.scope)
            self.history.start(cursor - 600, now)
        for inst in self.instruments():
            self._evaluate(inst)
        self._run_auto_ai()
        self.updated.emit("status")

    def fetch_candles(self, inst, bar):
        self.chart_feed.fetch(inst, bar)

    def select(self, inst, bar=None):
        self.selected = instrument_id(inst)
        if bar:
            if bar not in ("1m", "5m", "15m", "1H", "4H", "1D"):
                raise ValueError("无效的图表周期")
            self.bar = bar
        if self.running:
            self.chart_feed.activate()
        self.refresh_market()
        self.fetch_candles(self.selected, self.bar)
        self.updated.emit("selection")

    def set_chart_pairs(self, pairs):
        from coinpilot_ai.market.intervals import BARS
        self.chart_pairs = {(instrument_id(inst), bar) for inst, bar in pairs if bar in BARS}
        if self.running:
            self.chart_feed.activate()
            for pair in self.chart_pairs:
                self.fetch_candles(*pair)
            self.refresh_market()

    def set_indicator_pairs(self, pairs):
        from coinpilot_ai.market.intervals import BARS
        normalized = {(instrument_id(inst), bar) for inst, bar in pairs if bar in BARS}
        if normalized == self.indicator_pairs:
            return
        self.indicator_pairs = normalized
        if self.running:
            self.chart_feed.activate()
            for pair in normalized:
                self.fetch_candles(*pair)

    def save_watchlist(self, values):
        self.settings["watchlist"] = list(dict.fromkeys(instrument_id(v) for v in values if v.strip()))
        self.save_settings()
        self.refresh_market()

    def save_settings(self):
        self.store.put("settings", "workbench", self.settings)
        self.updated.emit("settings")

    def refresh_account(self):
        if self.closed or self.account_busy or not self.account_connected:
            return
        self.account_busy = True
        generation = self.generation
        result, errors = {}, []
        jobs = [
            ("config", "/api/v5/account/config", {}),
            ("balance", "/api/v5/account/balance", {"ccy": "USDT"}),
            ("positions", "/api/v5/account/positions", {"instType": "SWAP"}),
            ("orders", "/api/v5/trade/orders-pending", {"instType": "SWAP", "limit": "100"}),
            ("algos", "/api/v5/trade/orders-algo-pending", {"instType": "SWAP", "ordType": "conditional,oco", "limit": "100"}),
        ]
        received_times = []
        def next_job():
            if self.closed or generation != self.generation:
                return
            if not jobs:
                self.account_busy = False
                if errors:
                    self.account_error = "；".join(errors)
                    self.account["time"] = 0
                    self.had_account_failure = True
                else:
                    self.account = dict((result["config"] or [{}])[0], time=min(received_times))
                    self.balance = (result["balance"] or [{}])[0]
                    self.positions = [p for p in result["positions"] if p.get("instId", "").endswith("-USDT-SWAP") and number(p.get("pos") or "0")]
                    self.pending_orders = [o for o in result["orders"] if o.get("instId", "").endswith("-USDT-SWAP")]
                    self.algos = [o for o in result["algos"] if o.get("instId", "").endswith("-USDT-SWAP")]
                    self.account_error = ""
                    for order in self.pending_orders:
                        self._observe_order(order)
                    if self.had_account_failure:
                        self.awaiting_recovery = True
                        self.trading.recover()
                        self.had_account_failure = False
                        self._check_recovery()
                    if any(a.get("slTriggerPx") and a.get("tpTriggerPx") and a.get("state") == "live" for a in self.algos):
                        self.mark_demo("protection", {"time": time.time()})
                self.updated.emit("account")
                return
            name, path, params = jobs.pop(0)
            accumulated = []
            cursors = set()
            def received(rows, error):
                if self.closed or generation != self.generation:
                    return
                if error:
                    errors.append(str(error))
                elif name in ("config", "balance") and not rows:
                    errors.append("账户接口返回空数据，不能确认账户状态")
                else:
                    accumulated.extend(rows)
                    if name in ("orders", "algos") and len(rows) == 100:
                        key = "ordId" if name == "orders" else "algoId"
                        cursor = rows[-1].get(key)
                        if cursor and cursor not in cursors:
                            cursors.add(cursor)
                            params["after"] = cursor
                            self.api.get(path, received, params, private=True)
                            return
                        errors.append("挂单分页不完整")
                    result[name] = accumulated
                    received_times.append(time.time())
                next_job()
            self.api.get(path, received, params, private=True)
        next_job()

    def change_account(self, environment, credentials=None):
        if environment not in ("paper", "demo", "live"):
            raise ValueError("无效的环境")
        if credentials is not None and environment != 'paper':
            if not all(credentials.get(k, "").strip() for k in ("key", "secret", "passphrase")):
                raise ValueError("API Key、Secret 和 Passphrase 都需要填写")
            self.vault.write("okx/" + environment, credentials)
        self.generation += 1
        self.chart_feed.reset()
        self.derivatives.reset()
        self.last_derivative_poll = 0
        self.candle_times.clear()
        self.history.cancel()
        self.trading.close()
        self.transport.cancel_all()
        self.ai_transport.cancel_all()
        self.ai_queue.clear()
        self.actions_reconciling.clear()
        self.environment = self.settings["environment"] = environment
        self.positions, self.pending_orders, self.algos = [], [], []
        self.account, self.balance, self.leverages = {}, {}, {}
        self.account_busy = False
        self.had_account_failure = False
        self.awaiting_recovery = False
        self.account_error = "账户重新连接中"
        self.engine.invalidate()
        self._configure_account()
        self.last_history = 0
        self.save_settings()
        self.refresh_account()
        self.updated.emit("environment")
        if self.running:
            self.chart_feed.activate()
            self.fetch_candles(self.selected, self.bar)

    def set_proxy(self, config):
        if {k: v for k, v in config.items() if k.startswith("proxy_")} == {k: v for k, v in self.config.items() if k.startswith("proxy_")}:
            return
        self.config = dict(config)
        self.transport.set_proxy(config)
        self.ai_transport.set_proxy(config)
        self.market.set_proxy(config)
        self.chart_feed.reset()
        self.derivatives.reset()
        self.last_derivative_poll = 0
        self.candle_times.clear()
        if self.running:
            self.chart_feed.activate()
        self.engine.invalidate()
        self.quotes.clear()
        self.account["time"] = 0
        self.refresh_market()
        self.updated.emit("proxy")

    def rules(self):
        return self.store.list("rule", self.environment)

    def save_rule(self, rule, key=None):
        rule = validate_rule(rule)
        rule["version"] = 2
        key = key or uuid.uuid4().hex
        self.store.put("rule", key, rule, self.environment)
        self.engine.states.pop(key, None)
        self.refresh_market()
        self.updated.emit("rules")
        return key

    def _evaluate(self, inst):
        now = time.time()
        fresh_candles = {key: value for key, value in self.candles.items() if now - self.candle_times.get(key, 0) <= 30}
        for key, rule in self.rules():
            if rule["instrument"] != inst:
                continue
            try:
                event = self.engine.evaluate(key, rule, self.quotes.get(inst), self.positions, fresh_candles,
                                             now, now - self.account.get("time", 0) <= 20, self.derivatives)
            except (ValueError, KeyError, ArithmeticError):
                continue
            if event:
                self.store.put("rule_state", key, asdict(self.engine.states[key]), self.scope)
                self.record_event(event)

    def record_event(self, event):
        event = dict(event, context=self.context(event.get("instrument")), ai_status="未请求")
        key = self.store.append("event", event, self.scope)
        self.event_created.emit(key, event)
        self.updated.emit("events")
        if self.settings.get("auto_explain") and self.scene_service("event"):
            self.ai_queue.append((key, self.scope))
        return key

    def _observe_order(self, row):
        key, state = row.get("ordId"), row.get("state", "")
        if not key:
            return
        prior = self.store.get("order_seen", key, None, self.scope)
        marker = state + ":" + row.get("accFillSz", "")
        self.store.put("orders", key, row, self.scope)
        self.store.put("order_seen", key, marker, self.scope)
        if prior == marker or int(row.get("uTime") or "0") / 1000 < self.started_at:
            return
        if state not in ("filled", "partially_filled", "canceled", "failed"):
            return
        for rule_id, rule in self.rules():
            if rule.get("enabled", True) and rule["mode"] == "order" and rule["instrument"] == row["instId"] and state in rule["states"]:
                self.record_event({"name": rule["name"], "rule_id": rule_id, "instrument": row["instId"],
                                   "time": time.time(), "source": "paper" if self.environment == "paper" else "okx", "priority": "order", "order": row})
        local_id = row.get("clOrdId")
        local = self.store.get("local_order", local_id, None, self.scope) if local_id else None
        if local:
            local.update(status=state, order_id=key)
            self.store.put("local_order", local_id, local, self.scope)
            if state == "filled":
                self.mark_demo(local["draft"]["action"], {"order_id": key, "time": time.time()})
            if state == "canceled":
                self.mark_demo("cancel", {"order_id": key, "time": time.time()})

    def _local_order_changed(self, row):
        if self.environment == 'paper':
            row['protection'] = row.get('protection', '').replace('交易所已生成，详见止盈止损订单', '本地止盈止损已生效').replace('交易所', '本地模拟')
            self.store.put('local_order', row['client_id'], row, self.scope)
        if row["status"] == "failed":
            marker = "failed:" + row["client_id"]
            if not self.store.get("order_seen", marker, False, self.scope):
                self.store.put("order_seen", marker, True, self.scope)
                self.record_event({"name": "订单提交失败", "instrument": row["draft"]["instrument"],
                                   "time": time.time(), "source": "paper" if self.environment == "paper" else "okx", "priority": "order", "error": row.get("error")})
        if row["status"] == "filled":
            self.mark_demo(row["draft"]["action"], {"order_id": row.get("order_id"), "time": time.time()})
        if row["status"] == "canceled":
            self.mark_demo("cancel", {"order_id": row.get("order_id"), "time": time.time()})
        self._check_recovery()
        self.updated.emit("orders")

    def _check_recovery(self):
        if self.awaiting_recovery and time.time() - self.account.get("time", 0) <= 20:
            unresolved = any(row["status"] in ("submitting", "unknown") for _, row in self.store.list("local_order", self.scope))
            unresolved = unresolved or any(row["status"] in ("submitting", "unknown") for _, row in self.store.list("action", self.scope))
            if not unresolved:
                self.mark_demo("recovery", {"time": time.time(), "account_and_orders_refreshed": True})
                self.awaiting_recovery = False

    def _history_row(self, kind, row):
        if kind == "orders":
            self._observe_order(row)

    def _history_finished(self, coverage):
        self.updated.emit("history")

    def mark_demo(self, check, evidence):
        if self.environment == "demo" and check in DEMO_CHECKS:
            self.store.put("demo_check", check, dict(evidence, scope=self.scope, schema=1))

    def trading_allowed(self):
        if self.environment in ("demo", "paper"):
            return True
        return all(self.store.get("demo_check", key, {}).get("schema") == 1 for key in DEMO_CHECKS)

    def validate_order(self, draft, client_id):
        self._check_pending_action(draft.instrument)
        if self.environment == 'paper':
            self.refresh_account()
        payload = draft.payload(self.specs.get(draft.instrument, {}), self.account, self.positions,
                                self.quotes.get(draft.instrument), time.time(), client_id,
                                paper=self.environment == 'paper')
        if self.environment == 'paper':
            self.api.validate(payload)
        return payload

    def trade_action(self, path, payload, callback):
        if not self.trading_allowed() or time.time() - self.account.get("time", 0) > 20:
            raise ValueError("交易未解锁或账户状态过期")
        if path not in ("/api/v5/trade/order-algo", "/api/v5/trade/cancel-order", "/api/v5/trade/cancel-algos", "/api/v5/account/set-leverage"):
            raise ValueError("不允许此交易操作")
        target = payload[0] if isinstance(payload, list) else payload
        self._check_pending_action(target["instId"])
        key = self.store.append("action", {"path": path, "payload": payload, "time": time.time(), "status": "submitting"}, self.scope)
        scope, generation = self.scope, self.generation
        def done(rows, error):
            if self.closed or generation != self.generation:
                return
            if not error and not rows:
                error = ApiError("交易所未返回操作结果，请刷新检查实际状态", uncertain=True)
            elif not error and any(str(r.get("sCode", "0")) != "0" for r in rows):
                error = ApiError("交易所未接受操作，请刷新检查实际状态")
            row = self.store.get("action", key, {}, scope)
            row.update(status="unknown" if error and error.uncertain else "failed" if error else "accepted", error=str(error) if error else "")
            self.store.put("action", key, row, scope)
            self.updated.emit("orders")
            self.refresh_account()
            callback(rows, error)
            if row["status"] == "unknown":
                self.recover_actions()
        return self.api.post(path, payload, done)

    def _check_pending_action(self, inst):
        for _, row in self.store.list("action", self.scope):
            payload = row["payload"][0] if isinstance(row["payload"], list) else row["payload"]
            if payload.get("instId") == inst and row["status"] in ("submitting", "unknown"):
                raise ValueError("该合约有交易操作结果待确认，请先查询结果，避免重复提交")

    def recover_actions(self):
        if self.closed:
            return
        for key, row in self.store.list("action", self.scope):
            if row["status"] not in ("submitting", "unknown") or key in self.actions_reconciling:
                continue
            payload = row["payload"][0] if isinstance(row["payload"], list) else row["payload"]
            path = row["path"]
            if path.endswith("/order-algo"):
                endpoint, params = "/api/v5/trade/order-algo", {"algoClOrdId": payload.get("algoClOrdId")}
            elif path.endswith("/cancel-algos"):
                endpoint, params = "/api/v5/trade/order-algo", {"algoId": payload.get("algoId")}
            elif path.endswith("/cancel-order"):
                endpoint, params = "/api/v5/trade/order", {"instId": payload["instId"], "ordId": payload["ordId"]}
            elif path.endswith("/set-leverage"):
                endpoint, params = "/api/v5/account/leverage-info", {"instId": payload["instId"], "mgnMode": payload["mgnMode"]}
            else:
                continue
            self.actions_reconciling.add(key)
            generation, scope = self.generation, self.scope
            def done(rows, error, identity=key, original=row, action=path, data=payload, expected_generation=generation, original_scope=scope):
                self.actions_reconciling.discard(identity)
                if self.closed or expected_generation != self.generation:
                    return
                current = dict(original, status="unknown", error="结果待确认，不会自动重发。")
                if not error and rows:
                    verified = action.endswith("/order-algo")
                    if action.endswith(("/cancel-order", "/cancel-algos")):
                        verified = rows[0].get("state") in ("canceled", "filled", "effective", "order_failed")
                    if action.endswith("/set-leverage"):
                        verified = any(str(r.get("lever")) == str(data["lever"]) and (not data.get("posSide") or r.get("posSide") == data["posSide"]) for r in rows)
                    if verified:
                        current.update(status="confirmed", error="", result=rows, verified_at=time.time())
                self.store.put("action", identity, current, original_scope)
                self._check_recovery()
                self.updated.emit("orders")
            self.api.get(endpoint, done, params, private=True)

    def context(self, inst=None):
        inst = inst or self.selected
        fields = ("instId", "posSide", "pos", "availPos", "avgPx", "upl", "uplRatio", "lever", "mgnMode", "liqPx")
        return {"captured_at": time.time(), "environment": self.environment, "instrument": inst,
                "market": deepcopy(self.quotes.get(inst, {})), "bar": self.bar,
                "candles": deepcopy(self.candles.get((inst, self.bar), [])[-80:]),
                "positions": [{key: p.get(key, "") for key in fields} for p in self.positions if p.get("instId") == inst],
                "account_fresh": time.time() - self.account.get("time", 0) <= 20}

    def scene_service(self, scene):
        mapping = self.settings.get("scenes", {}).get(scene, {})
        return self.store.get("ai_service", mapping.get("service", ""))

    def scene_template(self, scene):
        mapping = self.settings.get("scenes", {}).get(scene, {})
        row = self.store.get("template", mapping.get("template", ""))
        return row if row and row["scenario"] == scene else self.prompts.templates(scene)[0][1]

    def ask_ai(self, scene, context, template=None, draft=False, event_id=None):
        config = self.scene_service(scene)
        if not config:
            raise ValueError("请先在设置中为该场景选择 AI 服务")
        template = deepcopy(template or self.scene_template(scene))
        prompt = render_prompt(template["body"], context)
        if len(prompt) > 240000:
            raise ValueError("所选上下文过大，请缩小交易范围后重试")
        task = uuid.uuid4().hex
        scope = self.scope
        record = {"scene": scene, "context": deepcopy(context), "template": template,
                  "service": {k: config[k] for k in ("name", "protocol", "model")},
                  "time": time.time(), "draft_requested": draft, "event_id": event_id, "status": "running"}
        self.ai_tasks[task] = {"request": None, "record": record, "scope": scope}
        def done(answer, error):
            if self.closed:
                return
            record.update(status="cancelled" if error and error.cancelled else "failed" if error else "complete",
                          answer=answer or "", error=str(error) if error else "")
            key = self.store.append("report" if scene == "review" else "analysis", record, scope)
            if event_id:
                event = self.store.get("event", event_id, {}, scope)
                event.update(ai_status=record["status"], analysis=answer or "", ai_error=record["error"], analysis_id=key)
                self.store.put("event", event_id, event, scope)
            self.ai_tasks.pop(task, None)
            self.ai_finished.emit(task, dict(record, id=key, scope=scope))
            self.updated.emit("reports" if scene == "review" else "events")
        request = self.ai.ask(config, prompt, done, draft)
        if task in self.ai_tasks:
            self.ai_tasks[task]["request"] = request
        return task

    def cancel_ai(self, task):
        entry = self.ai_tasks.get(task)
        if entry and entry["request"]:
            self.ai_transport.cancel(entry["request"])

    def _run_auto_ai(self):
        if self.ai_queue and not self.ai_tasks:
            key, scope = self.ai_queue.pop(0)
            if scope != self.scope:
                return
            event = self.store.get("event", key, {}, scope)
            try:
                self.ask_ai("event", dict(event.get("context", {}), events=[{k: v for k, v in event.items() if k != "context"}]), event_id=key)
            except ValueError as exc:
                event.update(ai_status="failed", ai_error=str(exc))
                self.store.put("event", key, event, scope)

    def journal(self):
        trades, unassigned = aggregate([r for _, r in self.store.list("fills", self.scope)],
                                       [r for _, r in self.store.list("bills", self.scope)],
                                       [r for _, r in self.store.list("orders", self.scope)])
        local = {r.get("order_id"): r for _, r in self.store.list("local_order", self.scope) if r.get("order_id")}
        for trade in trades:
            trade["recorded_context"] = [local[oid] for oid in trade["order_ids"] if oid in local]
            note = self.store.get("journal_note", trade["id"], {}, self.scope)
            if not note and trade["recorded_context"]:
                draft = trade["recorded_context"][0].get("draft", {})
                note = {"reason": draft.get("reason", ""),
                        "tags": [tag.strip() for tag in draft.get("tags", "").split(",") if tag.strip()]}
            trade["note"] = note
            if len(trade["recorded_context"]) < len(trade["order_ids"]):
                trade["gaps"].append("部分交易来自外部或接入前，缺少本软件记录的理由与当时分析")
        return trades, unassigned

    def review_context(self, selected, begin, end):
        all_trades, unassigned = self.journal()
        wanted = set(selected)
        trades = [row for row in all_trades if row["id"] in wanted] if wanted else [row for row in all_trades if row["start"] <= end * 1000 and (row["end"] or time.time() * 1000) >= begin * 1000]
        if not trades:
            raise ValueError("当前选择没有可复盘交易，请同步历史或调整范围")
        selected_ids = {trade["id"] for trade in trades}
        unassigned = [cost for cost in unassigned if cost.get("trade") in selected_ids or
                      any(trade["instrument"] == cost.get("bill", {}).get("instId") and
                          trade["start"] <= int(cost["bill"]["ts"]) <= (trade["end"] or time.time()*1000)
                          for trade in trades)]
        return {"range": {"begin": begin, "end": end}, "selection": "explicit" if wanted else "overlapping_range",
                "environment": self.environment, "trades": trades, "statistics": summarize(trades),
                "unassigned_costs": unassigned,
                "sync_runs": [r for _, r in self.store.list("sync_run", self.scope, limit=5)],
                "boundary_note": "跨时间范围的交易展示完整已知过程；历史起点之前的仓位可能未知。只对完整已知交易统计胜负。"}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.history.cancel()
        self.trading.close()
        self.market.close()
        self.chart_feed.close()
        self.derivatives.reset()
        self.transport.close()
        self.ai_transport.close()
        self.store.close()
