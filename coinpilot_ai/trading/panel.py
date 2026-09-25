"""交易页：订单草稿、人工确认、仓位与止盈止损操作。"""
import time
import uuid

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from .models import OrderDraft, aligned, instrument_id, number
from .risk import position_plan
from coinpilot_ai.review.equity_curve import EquityCurve
from coinpilot_ai.ui.common import AccountTabs, button, confirm, fill_table, selected_id, table, timestamp
from coinpilot_ai.ui.common import combo, select_data

STATES = {"submitting": "提交中", "unknown": "结果待确认", "live": "挂单中", "partially_filled": "部分成交", "filled": "已成交", "canceled": "已撤销", "failed": "失败", "accepted": "已接受", "confirmed": "已核实"}


class RiskDialog(QDialog):
    """线性合约仓位测算；只有本地模拟环境可把结果带入草稿。"""

    def __init__(self, service, instrument, apply_draft, parent=None):
        super().__init__(parent)
        self.service, self.instrument, self.apply_draft = service, instrument, apply_draft
        self.setWindowTitle("风险与仓位计算器")
        self.resize(410, 340)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.direction = combo([("long", "做多"), ("short", "做空")])
        quote = service.quotes.get(instrument, {})
        self.fields = {}
        defaults = {"equity": str(service.balance.get("totalEq", "")), "risk_percent": "1",
                    "entry": str(quote.get("price", "")), "stop": "", "target": ""}
        for key, label in (("equity", "账户资产 USDT"), ("risk_percent", "风险比例 %"),
                           ("entry", "入场价"), ("stop", "止损价"), ("target", "止盈价（可选）")):
            field = QLineEdit(defaults[key])
            self.fields[key] = field
            form.addRow(label, field)
        form.addRow("方向", self.direction)
        layout.addLayout(form)
        self.result = QLabel("填写计划价格后计算。手续费与滑点未包含在风险预算内。")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        self.plan = None
        layout.addWidget(button("计算仓位", self.calculate))
        self.use_button = button("填入本地模拟订单草稿", self.use_plan)
        self.use_button.setEnabled(False)
        layout.addWidget(self.use_button)

    def calculate(self):
        try:
            values = {key: field.text().strip() for key, field in self.fields.items()}
            self.plan = position_plan(**values, direction=self.direction.currentData(),
                                      spec=self.service.specs[self.instrument])
            plan = self.plan
            self.result.setText(f"最大风险 {plan['risk_budget']:,.4f} USDT · 止损距离 {plan['stop_percent']:.2f}%\n"
                                f"建议 {plan['contracts']} 张 · 名义仓位 {plan['notional']:,.4f} USDT\n"
                                f"按计划价格亏损 {plan['planned_loss']:,.4f} USDT"
                                + (f" · 盈亏比 1:{plan['risk_reward']:.2f}" if plan['risk_reward'] is not None else "")
                                + ("\n风险预算不足最小下单张数。" if plan['below_minimum'] else ""))
            self.use_button.setEnabled(self.service.environment == "paper" and not plan["below_minimum"])
        except (ValueError, KeyError) as exc:
            self.plan = None
            self.use_button.setEnabled(False)
            self.result.setText(str(exc))

    def use_plan(self):
        if self.plan and self.service.environment == "paper":
            self.apply_draft(self.plan, dict({key: field.text().strip() for key, field in self.fields.items()},
                                             instrument=self.instrument))
            self.accept()


class TradeController(QObject):
    """共享交易操作；表单与账户表格由工作区分别托管。"""
    form_requested = pyqtSignal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.source = "manual"
        self.leverage_pending = set()
        self.action_busy = False
        self.last_submission_id = None
        self.bottom = QWidget()
        bottom_layout = QVBoxLayout(self.bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(2)
        self.tabs = AccountTabs()
        bottom_layout.addWidget(self.tabs.selector)
        self.position_table = table(["合约", "方向 / 模式", "持仓 / 可平(张)", "开仓均价", "未实现盈亏", "杠杆"], readable=True)
        self.order_table = table(["合约", "买卖 / 类型", "数量(张)", "价格", "状态"], readable=True)
        self.algo_table = table(["合约", "买卖", "张数", "止损", "止盈", "状态"], readable=True)
        self.local_table = table(["时间", "合约", "开平 / 方向", "状态", "止盈止损 / 结果"], readable=True)
        self.fill_table = table(["时间", "合约", "买卖", "张数", "成交价", "费用"], readable=True)
        for title, widget in (("持仓", self.position_table), ("挂单", self.order_table), ("止盈止损", self.algo_table), ("本机提交", self.local_table), ("成交", self.fill_table)):
            self.tabs.addTab(widget, title)
        self.equity_curve = EquityCurve(service.store, "paper:local")
        bottom_layout.addWidget(self.tabs, 1)
        self.action_buttons = [button("载入平仓", self.load_position),
            button("止盈止损", self.protect_position), button("撤销订单", self.cancel_order),
            button("查询结果", lambda: (self.service.trading.recover(), self.service.recover_actions()))]
        actions = QHBoxLayout()
        for btn in self.action_buttons:
            actions.addWidget(btn)
        actions.addStretch()
        bottom_layout.addLayout(actions)
        self.operation_feedback = QLabel()
        self.operation_feedback.setWordWrap(True)
        self.operation_feedback.setMaximumHeight(36)
        self.operation_feedback.hide()
        bottom_layout.addWidget(self.operation_feedback)
        self.tabs.currentChanged.connect(self.update_actions)
        self.update_actions()
        sidebar = QWidget()
        sidebar.setObjectName("sidePanel")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(9, 8, 9, 8)
        side_layout.setSpacing(7)
        heading = QLabel("委托下单")
        heading.setObjectName("sectionTitle")
        side_layout.addWidget(heading)
        self.paper_button = button('开启本地模拟（免 API）', lambda: service.change_account('paper'))
        side_layout.addWidget(self.paper_button)
        self.paper_hint = QLabel()
        self.paper_hint.setWordWrap(True)
        side_layout.addWidget(self.paper_hint)
        form_widget = QWidget()
        form_widget.setObjectName("tradeFormContent")
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.inst = QLineEdit(service.selected)
        self.inst.editingFinished.connect(self.select_instrument)
        self.action = combo([("open:long", "开多"), ("open:short", "开空"), ("close:long", "平多"), ("close:short", "平空")])
        self.order_type = combo([("market", "市价"), ("limit", "限价"), ("stop", "Stop（仅本地模拟）"),
                                 ("stop_limit", "Stop Limit（仅本地模拟）"),
                                 ("trailing_stop", "Trailing Stop（仅本地模拟）")])
        self.margin = combo([("cross", "全仓"), ("isolated", "逐仓")])
        self.size = QLineEdit()
        self.size.setPlaceholderText("合约张数，不是币数量")
        self.price, self.sl, self.tp = QLineEdit(), QLineEdit(), QLineEdit()
        self.trigger_price, self.trailing_offset = QLineEdit(), QLineEdit()
        self.reason, self.tags = QLineEdit(), QLineEdit()
        self.reason.setPlaceholderText("可选，保存供复盘使用")
        self.leverage = QLabel("待查询")
        for title, widget in (("合约", self.inst), ("操作", self.action), ("保证金", self.margin), ("订单类型", self.order_type),
                              ("数量(张)", self.size), ("限价", self.price),
                              ("Stop 触发价", self.trigger_price), ("追踪距离", self.trailing_offset),
                              ("止损触发价", self.sl), ("止盈触发价", self.tp),
                              ("实际杠杆", self.leverage), ("交易理由", self.reason), ("标签", self.tags)):
            form.addRow(title, widget)
        self.order_type.currentIndexChanged.connect(self.sync_order_type)
        self.price.setEnabled(False)
        self.sync_order_type()
        self.margin.currentIndexChanged.connect(self.refresh_leverage)
        self.action.currentIndexChanged.connect(self.refresh_leverage)
        form_layout.addLayout(form)
        form_layout.addWidget(button("风险与仓位计算器", self.open_risk))
        form_layout.addWidget(button("查询／调整杠杆", self.change_leverage))
        self.spec_label = QLabel("等待合约规格")
        self.spec_label.setWordWrap(True)
        form_layout.addWidget(self.spec_label)
        self.submit_button = button("检查并确认下单", self.submit, True)
        self.feedback = QLabel("所有订单均需确认。止盈止损以交易所实际生成状态为准。")
        self.feedback.setWordWrap(True)
        form_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        side_layout.addWidget(scroll, 1)
        side_layout.addWidget(self.submit_button)
        side_layout.addWidget(self.feedback)
        sidebar.setMinimumWidth(300)
        self.sidebar = sidebar
        self.bottom.setMinimumSize(300, 190)

    def set_feedback(self, text):
        self.feedback.setText(text)
        self.operation_feedback.setText(text)
        self.operation_feedback.setToolTip(text)
        self.operation_feedback.setVisible(bool(text))

    def update_actions(self, *_):
        index = self.tabs.currentIndex()
        for btn, visible in zip(self.action_buttons, (index == 0, index == 0, index in (1, 2), index == 3)):
            btn.setVisible(visible)

    def draft(self):
        action, direction = self.action.currentData().split(":")
        return OrderDraft(instrument_id(self.inst.text()), action, direction, self.order_type.currentData(), self.size.text().strip(),
                          self.price.text().strip(), self.margin.currentData(), self.sl.text().strip(), self.tp.text().strip(),
                          self.reason.text().strip(), self.tags.text().strip(), self.source,
                          self.trigger_price.text().strip(), self.trailing_offset.text().strip())

    def sync_order_type(self):
        kind = self.order_type.currentData()
        self.price.setEnabled(kind in ("limit", "stop_limit"))
        self.trigger_price.setEnabled(kind in ("stop", "stop_limit"))
        self.trailing_offset.setEnabled(kind == "trailing_stop")

    def open_risk(self):
        try:
            instrument = instrument_id(self.inst.text())
        except ValueError as exc:
            self.set_feedback(str(exc))
            return
        RiskDialog(self.service, instrument, self.apply_risk_plan, self.sidebar).exec()

    def apply_risk_plan(self, plan, fields):
        if self.service.environment != "paper":
            return
        self.inst.setText(fields["instrument"])
        self.select_instrument()
        select_data(self.action, "open:" + plan["direction"])
        self.size.setText(str(plan["contracts"]))
        self.price.setText(fields["entry"])
        self.sl.setText(fields["stop"])
        self.tp.setText(fields["target"])
        select_data(self.order_type, "limit")
        self.set_feedback("已填入本地模拟草稿；请检查价格、张数和费用后人工确认。")

    def ai_context(self):
        context = self.service.context(instrument_id(self.inst.text()))
        context["order_draft"] = self.draft().to_dict()
        context["contract_spec"] = self.service.specs.get(context["instrument"], {})
        return context

    def select_instrument(self):
        try:
            self.service.select(self.inst.text())
            self.inst.setText(self.service.selected)
            self.refresh_leverage()
        except ValueError as exc:
            self.set_feedback(str(exc))

    def load_draft(self, draft):
        self.form_requested.emit()
        self.source = draft.source
        self.inst.setText(draft.instrument)
        select_data(self.action, draft.action + ":" + draft.direction)
        select_data(self.order_type, draft.order_type)
        select_data(self.margin, draft.margin)
        for widget, value in ((self.size, draft.size), (self.price, draft.price), (self.sl, draft.stop_loss),
                              (self.tp, draft.take_profit), (self.reason, draft.reason), (self.tags, draft.tags),
                              (self.trigger_price, draft.trigger_price), (self.trailing_offset, draft.trailing_offset)):
            widget.setText(value)
        self.select_instrument()
        self.set_feedback("草稿已载入，请检查合约张数、价格与方向，再确认提交。")

    def submit(self):
        token = None
        try:
            draft = self.draft()
            self.refresh_leverage()
            lever = self.current_leverage()
            if not lever:
                raise ValueError("尚未获取当前保证金模式的实际杠杆，请等待查询完成")
            context = self.ai_context()
            token, payload = self.service.trading.prepare(draft, context)
            env = self.service.environment_label
            summary = f"{env} · {draft.instrument}\n{self.action.currentText()} / {self.margin.currentText()} / {self.order_type.currentText()}\n数量：{draft.size} 张\n价格：{draft.price if draft.order_type in ('limit', 'stop_limit') else '触发后按市场成交'}\n触发价：{draft.trigger_price or '未设置'}  追踪距离：{draft.trailing_offset or '未设置'}\n实际杠杆：{lever} 倍\n止损：{draft.stop_loss or '未设置'}  止盈：{draft.take_profit or '未设置'}"
            if not confirm(self.sidebar, "确认提交订单", summary):
                self.service.trading.prepared.pop(token, None)
                return
            client_id = self.service.trading.submit(token, confirmed=True)
            self.last_submission_id = client_id
            self.set_feedback(("本地模拟订单已提交：" if self.service.environment == 'paper' else "已提交，正在等待交易所确认：") + client_id)
            self.refresh('orders')
            self.source = "manual"
        except (ValueError, OSError) as exc:
            if token:
                self.service.trading.prepared.pop(token, None)
            self.set_feedback(str(exc))

    def selected_position(self):
        key = selected_id(self.position_table)
        row = next((p for p in self.service.positions if p.get("posId") == key), None)
        if not row:
            raise ValueError("请先选择一个持仓")
        return row

    def load_position(self):
        try:
            p = self.selected_position()
            side = p["posSide"] if p["posSide"] != "net" else "long" if number(p["pos"]) > 0 else "short"
            self.load_draft(OrderDraft(p["instId"], action="close", direction=side, size=str(abs(number(p.get("availPos") or p["pos"]))), margin=p["mgnMode"]))
            self.set_feedback("已填入全部可平张数；可修改为部分平仓，再检查并确认。")
        except ValueError as exc:
            self.set_feedback(str(exc))

    def current_leverage(self):
        key = (self.inst.text(), self.margin.currentData())
        cached = self.service.leverages.get(key)
        if not cached or time.time() - cached["time"] > 30:
            return None
        side = self.action.currentData().split(":")[1]
        return next((r.get("lever") for r in cached["rows"] if r.get("posSide") in (None, "", "net", side)), None)

    def refresh_leverage(self):
        try:
            inst = instrument_id(self.inst.text())
        except ValueError:
            return
        if not self.service.account_connected:
            self.leverage.setText("尚未连接账户")
            return
        key = (inst, self.margin.currentData())
        current = self.current_leverage()
        self.leverage.setText((str(current) + " 倍") if current else "查询中…")
        if current or key in self.leverage_pending:
            return
        self.leverage_pending.add(key)
        generation = self.service.generation
        def done(rows, error):
            self.leverage_pending.discard(key)
            if self.service.closed or generation != self.service.generation:
                return
            if error:
                self.leverage.setText(str(error))
            else:
                self.service.leverages[key] = {"rows": rows, "time": time.time()}
                value = self.current_leverage()
                self.leverage.setText((str(value) + " 倍") if value else "未获取到杠杆")
        self.service.api.get("/api/v5/account/leverage-info", done, {"instId": inst, "mgnMode": key[1]}, private=True)

    def change_leverage(self):
        self.refresh_leverage()
        local = self.service.environment == 'paper'
        value, accepted = QInputDialog.getInt(self.sidebar, "调整模拟杠杆" if local else "调整交易所杠杆", "新的杠杆倍数", int(number(self.current_leverage() or "1")), 1, 125)
        if not accepted:
            return
        try:
            payload = {"instId": instrument_id(self.inst.text()), "mgnMode": self.margin.currentData(), "lever": str(value)}
            if self.service.account.get("posMode") == "long_short_mode" and payload["mgnMode"] == "isolated":
                payload["posSide"] = self.action.currentData().split(":")[1]
            if confirm(self.sidebar, "确认修改杠杆", f"{self.service.environment_label} · {payload['instId']} / {self.margin.currentText()}\n将杠杆修改为 {value} 倍。"):
                self.perform("/api/v5/account/set-leverage", payload)
                self.service.leverages.clear()
        except ValueError as exc:
            self.set_feedback(str(exc))

    def perform(self, path, payload):
        if self.action_busy:
            raise ValueError("上一操作仍在等待结果")
        self.action_busy = True
        def done(rows, error):
            self.action_busy = False
            self.set_feedback((str(error) + ("；结果待确认，请刷新状态，不要重复提交。" if error.uncertain else "")) if error else ("本地模拟操作已完成。" if self.service.environment == 'paper' else "交易所已接受操作，等待状态同步。"))
            self.service.leverages.clear()
        try:
            self.service.trade_action(path, payload, done)
        except Exception:
            self.action_busy = False
            raise

    def cancel_order(self):
        try:
            is_algo = self.tabs.currentWidget() == self.algo_table
            widget = self.algo_table if is_algo else self.order_table
            identity = selected_id(widget)
            rows = self.service.algos if is_algo else self.service.pending_orders
            key = "algoId" if is_algo else "ordId"
            row = next((r for r in rows if r.get(key) == identity), None)
            if not row:
                raise ValueError("请在挂单或止盈止损页选择要撤销的订单")
            if confirm(self.sidebar, "确认撤销订单", f"{self.service.environment_label} · {row['instId']}\n订单 {identity}\n" + ("将撤销这笔止盈止损保护。" if is_algo else "仅撤销本地模拟挂单。" if self.service.environment == 'paper' else "撤单结果以交易所为准。")):
                payload = {"instId": row["instId"], key: identity}
                self.perform("/api/v5/trade/cancel-algos" if is_algo else "/api/v5/trade/cancel-order", [payload] if is_algo else payload)
        except ValueError as exc:
            self.set_feedback(str(exc))

    def protect_position(self):
        try:
            position = self.selected_position()
            dialog = QDialog(self.bottom)
            dialog.setWindowTitle(self.service.environment_label + " · 设置止盈止损")
            layout = QVBoxLayout(dialog)
            form = QFormLayout()
            size = QLineEdit(str(abs(number(position.get("availPos") or position["pos"]))))
            sl, tp = QLineEdit(), QLineEdit()
            form.addRow("数量(张)", size)
            form.addRow("止损触发价", sl)
            form.addRow("止盈触发价", tp)
            layout.addLayout(form)
            layout.addWidget(QLabel("使用最新成交价触发，市价退出；已有保护单不会自动取消。"))
            buttons = QDialogButtonBox()
            buttons.addButton("检查并确认", QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            inst = position["instId"]
            direction = position["posSide"] if position["posSide"] != "net" else "long" if number(position["pos"]) > 0 else "short"
            draft = OrderDraft(inst, action="close", direction=direction, size=size.text(), margin=position["mgnMode"])
            payload = self.service.validate_order(draft, "cw" + uuid.uuid4().hex[:26])
            payload.pop("clOrdId")
            payload["algoClOrdId"] = "cwa" + uuid.uuid4().hex[:25]
            payload["ordType"] = "oco" if sl.text().strip() and tp.text().strip() else "conditional"
            if not sl.text().strip() and not tp.text().strip():
                raise ValueError("至少填写一个止盈或止损价格")
            current = number(self.service.quotes[inst]["price"])
            for prefix, edit in (("sl", sl), ("tp", tp)):
                if edit.text().strip():
                    level = aligned(edit.text().strip(), self.service.specs[inst]["tickSz"], "触发价格")
                    above = (direction == "long") == (prefix == "tp")
                    if (above and level <= current) or (not above and level >= current):
                        raise ValueError("止盈止损触发方向与当前价格不匹配")
                    payload.update({prefix+"TriggerPx": str(level), prefix+"OrdPx": "-1", prefix+"TriggerPxType": "last"})
            if confirm(self.sidebar, "确认设置止盈止损", f"{self.service.environment_label} · {inst} · {size.text()} 张\n止损 {sl.text() or '无'} / 止盈 {tp.text() or '无'}"):
                self.perform("/api/v5/trade/order-algo", payload)
        except (ValueError, KeyError) as exc:
            self.set_feedback(str(exc))

    def refresh(self, kind):
        s = self.service
        self.paper_button.setVisible(s.environment == 'demo' and not s.account_connected)
        self.paper_hint.setVisible(s.environment == 'paper')
        if s.environment == 'paper':
            self.paper_hint.setText(f"虚拟资金 · 手续费率 {s.settings.get('paper_fee', '0.0005')} · "
                                    f"滑点 {s.settings.get('paper_slippage_bps', '0')} bps · 未模拟资金费")
        if kind in ("account", "history", "orders", "environment"):
            self.equity_curve.update()
        if kind == "environment":
            self.action_busy = False
            self.last_submission_id = None
            self.leverage_pending.clear()
            self.source = "manual"
            self.feedback.setText('本地虚拟资金，所有订单仍需确认。' if s.environment == 'paper' else '所有订单均需确认。止盈止损以交易所实际生成状态为准。')
            for edit in (self.size, self.price, self.sl, self.tp, self.reason, self.tags,
                         self.trigger_price, self.trailing_offset):
                edit.clear()
        if kind == "selection":
            self.inst.setText(s.selected)
            self.refresh_leverage()
        if kind in ("candles", "selection", "market"):
            spec = s.specs.get(s.selected, {})
            self.spec_label.setText(f"每张 {spec.get('ctVal', '—')} {spec.get('ctValCcy', '')}\n最小张数 {spec.get('minSz', '—')} · 数量步长 {spec.get('lotSz', '—')}\n价格步长 {spec.get('tickSz', '—')}")
        if kind in ("account", "environment"):
            self.refresh_leverage()
        if kind in ("account", "orders", "environment") and self.bottom.isVisible():
            fill_table(self.position_table, [(p.get("posId"), [p["instId"],
                ('多' if p['posSide'] == 'long' or p['posSide'] == 'net' and number(p['pos']) > 0 else '空') + ' / ' + {'cross': '全仓', 'isolated': '逐仓'}.get(p['mgnMode'], p['mgnMode']),
                str(abs(number(p['pos'])))+" / "+(p.get("availPos") or str(abs(number(p['pos'])))), p.get("avgPx"), p.get("upl"), p.get("lever")]) for p in s.positions])
            fill_table(self.order_table, [(o["ordId"], [o["instId"], {'buy': '买', 'sell': '卖'}.get(o.get('side'), '')+" / "+{'market': '市价', 'limit': '限价'}.get(o.get('ordType'), o.get('ordType', '')), o.get("sz"), o.get("px"), STATES.get(o.get("state"), o.get("state"))]) for o in s.pending_orders])
            fill_table(self.algo_table, [(o["algoId"], [o["instId"], o.get("side"), o.get("sz"), o.get("slTriggerPx"), o.get("tpTriggerPx"), o.get("state")]) for o in s.algos])
        if kind in ("orders", "history", "environment", "account"):
            if self.last_submission_id:
                recent = s.store.get("local_order", self.last_submission_id, scope=s.scope)
                if recent:
                    self.set_feedback(STATES.get(recent["status"], recent["status"]) + " · " + (recent.get("error") or recent.get("protection", "")))
            if not self.bottom.isVisible():
                return
            records = [(key, [timestamp(o["time"]), o["draft"]["instrument"], o["draft"]["action"]+" / "+o["draft"]["direction"], STATES.get(o["status"], o["status"]), o.get("error") or o.get("protection", "")]) for key, o in s.store.list("local_order", s.scope, limit=100)]
            names = {"order-algo": "设置止盈止损", "cancel-order": "撤单", "cancel-algos": "撤销保护", "set-leverage": "修改杠杆"}
            for key, action in s.store.list("action", s.scope, limit=100):
                payload = action["payload"][0] if isinstance(action["payload"], list) else action["payload"]
                records.append(("action:" + key, [timestamp(action["time"]), payload.get("instId", ""), names.get(action["path"].rsplit("/", 1)[-1], "操作"), STATES.get(action["status"], action["status"]), action.get("error", "")]))
            fill_table(self.local_table, records)
            fill_table(self.fill_table, [(key, [timestamp(int(o.get("fillTime") or o["ts"])/1000), o["instId"], o["side"], o["fillSz"], o["fillPx"], (o.get("fee") or "0")+" "+o.get("feeCcy", "")]) for key, o in s.store.list("fills", s.scope, limit=100)])
