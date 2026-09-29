"""只暴露回放游标以前 K 线的训练会话。"""
from copy import deepcopy
from uuid import uuid4

from coinpilot_ai.market.history import HistoryData
from coinpilot_ai.market.intervals import BARS, shift
from coinpilot_ai.trading.alerts import AlertEngine, RuleState
from coinpilot_ai.trading.paper import PaperBroker


class ReplaySession:
    def __init__(self, store, specs, instrument, bar, rows, session_id=None, *, fee="0.0005", slippage_bps="0"):
        if bar not in BARS:
            raise ValueError("不支持的回放周期")
        if not isinstance(rows, HistoryData):
            rows = HistoryData.create(rows, BARS[bar]*1000, bar=bar)
        if len(rows) < 2:
            rows.close()
            raise ValueError("回放至少需要两根已收盘 K 线")
        self.store, self.specs = store, specs
        self.instrument, self.bar, self.rows = instrument, bar, rows
        self.id = session_id or uuid4().hex
        self.scope = "replay:" + self.id
        saved = store.get("replay_session", self.id, {})
        self.fee = str(saved.get("fee", fee))
        self.slippage_bps = str(saved.get("slippage_bps", slippage_bps))
        self.cursor = min(max(0, int(saved.get("cursor", 0))), len(rows)-1)
        self.window = rows[max(0, self.cursor-5999):self.cursor+1]
        self.window_start = max(0, self.cursor-5999)
        self.pending_commands = saved.get("pending_commands", [])
        self.alert_engine = AlertEngine()
        self.alert_engine.states = {key: RuleState(**state)
                                    for key, state in saved.get("alert_states", {}).items()}
        self.time = self.stamp(self.cursor, end=True)
        self.quotes = {}
        self.set_quote(self.rows[self.cursor][4])
        self.broker = PaperBroker(store, None, lambda: specs, lambda: self.quotes, lambda: None,
                                  scope=self.scope, clock=lambda: self.time, fee=self.fee,
                                  slippage_bps=self.slippage_bps)
        self.save()

    def stamp(self, index, *, end=False):
        stamp = int(self.rows[index][0])
        return (shift(stamp, self.bar) if end else stamp)/1000

    def set_quote(self, price):
        self.quotes[self.instrument] = {"source": "okx", "price": str(price), "time": self.time}

    def visible(self):
        return self.window

    def close(self):
        self.rows.close()

    def submit(self, payload):
        if self.cursor >= len(self.rows)-1:
            raise ValueError("回放已到末尾")
        candidate = deepcopy(payload)
        if candidate.get("instId") != self.instrument:
            raise ValueError("订单合约与回放不一致")
        self.broker.validate(candidate)
        self.pending_commands.append(candidate)
        self.save()

    def step(self):
        if self.cursor >= len(self.rows)-1:
            return False
        next_index = self.cursor+1
        row = self.rows[next_index]
        self.time = self.stamp(next_index)
        self.set_quote(row[1])
        commands, self.pending_commands = self.pending_commands, []
        for command in commands:
            responses = []
            self.broker.post("/api/v5/trade/order", command, lambda rows, error: responses.append((rows, error)))
            if responses and responses[0][1]:
                self.store.append("replay_rejection", {"time": self.time, "order": command,
                    "error": str(responses[0][1])}, self.scope)
        self.broker.match_bar(self.instrument, row)
        self.time = self.stamp(next_index, end=True)
        self.set_quote(row[4])
        self.cursor = next_index
        self.window.append(row)
        if len(self.window) > 6000:
            dropped = len(self.window)-5000
            del self.window[:dropped]
            self.window_start += dropped
        self.save()
        return True

    def evaluate_rules(self, rules):
        """在训练时间轴上评估规则；事件只保存到训练作用域。"""
        quote = self.quotes[self.instrument]
        self.alert_engine.tick(self.instrument, quote["price"], self.time,
                               max_gap=(self.stamp(self.cursor, end=True)-self.stamp(self.cursor))+30)
        positions, _ = self.broker.snapshot()
        candles = {(self.instrument, self.bar): self.visible()}
        events = []
        for key, rule in rules:
            if rule.get("instrument") != self.instrument or rule.get("mode") == "order":
                continue
            event = self.alert_engine.evaluate(key, rule, quote, positions, candles, self.time,
                                               account_fresh=True)
            if event:
                event["source"] = "replay"
                self.store.append("replay_alert", event, self.scope)
                events.append(event)
        self.save()
        return events

    def save(self):
        self.store.put("replay_session", self.id, {"version": 1, "instrument": self.instrument,
            "bar": self.bar, "begin": int(self.rows[0][0]), "end": int(self.rows[-1][0]),
            "cursor": self.cursor, "pending_commands": deepcopy(self.pending_commands),
            "fee": self.fee, "slippage_bps": self.slippage_bps,
            "alert_states": {key: vars(state).copy() for key, state in self.alert_engine.states.items()}})
