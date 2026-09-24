"""交易页：订单草稿、人工确认、仓位与止盈止损操作。"""
import time
import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .chart_panel import ChartPanel, SavedSplitter
from .domain import OrderDraft, aligned, instrument_id, number
from .ui_ai import AiPanel
from .ui_common import button, confirm, fill_table, selected_id, table, timestamp
from .ui_settings import combo, select_data

STATES = {"submitting": "提交中", "unknown": "结果待确认", "live": "挂单中", "partially_filled": "部分成交", "filled": "已成交", "canceled": "已撤销", "failed": "失败", "accepted": "已接受", "confirmed": "已核实"}


class TradePage(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.source = "manual"
        self.leverage_pending = set()
        self.action_busy = False
        self.last_submission_id = None
        root = QVBoxLayout(self)
        root.setContentsMargins(3, 3, 3, 3)
        splitter = SavedSplitter(Qt.Orientation.Horizontal, service, "trade_horizontal")
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        self.vertical = SavedSplitter(Qt.Orientation.Vertical, service, "trade_vertical")
        self.chart = ChartPanel(service, trading=True)
        self.vertical.addWidget(self.chart)
        self.bottom = QWidget()
        bottom_layout = QVBoxLayout(self.bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(2)
        self.tabs = QTabWidget()
        self.position_table = table(["合约", "方向 / 模式", "持仓 / 可平(张)", "开仓均价", "未实现盈亏", "杠杆"])
        self.order_table = table(["合约", "买卖 / 类型", "数量(张)", "价格", "状态"])
        self.algo_table = table(["合约", "买卖", "张数", "止损", "止盈", "状态"])
        self.local_table = table(["时间", "合约", "开平 / 方向", "状态", "止盈止损 / 结果"])
        self.fill_table = table(["时间", "合约", "买卖", "张数", "成交价", "费用"])
        for title, widget in (("持仓", self.position_table), ("挂单", self.order_table), ("止盈止损", self.algo_table), ("本机提交", self.local_table), ("成交", self.fill_table)):
            self.tabs.addTab(widget, title)
        bottom_layout.addWidget(self.tabs, 1)
        actions = QGridLayout()
        actions.setSpacing(2)
        actions.addWidget(button("载入平仓", self.load_position), 0, 0)
        actions.addWidget(button("止盈止损", self.protect_position), 0, 1)
        actions.addWidget(button("撤销订单", self.cancel_order), 0, 2)
        actions.addWidget(button("查询结果", lambda: (self.service.trading.recover(), self.service.recover_actions())), 0, 3)
        bottom_layout.addLayout(actions)
        self.vertical.addWidget(self.bottom)
        self.vertical.restore([420, 190])
        layout.addWidget(self.vertical, 1)
        self.ai_panel = AiPanel(service, "order", self.ai_context)
        self.ai_panel.draft_ready.connect(self.load_draft)
        layout.addWidget(self.ai_panel)
        splitter.addWidget(left)
        sidebar = QWidget()
        sidebar.setObjectName("sidePanel")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(9, 8, 9, 8)
        side_layout.setSpacing(7)
        heading = QLabel("委托下单")
        heading.setObjectName("sectionTitle")
        side_layout.addWidget(heading)
        form_widget = QWidget()
        form_widget.setObjectName("tradeFormContent")
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.inst = QLineEdit(service.selected)
        self.inst.editingFinished.connect(self.select_instrument)
        self.action = combo([("open:long", "开多"), ("open:short", "开空"), ("close:long", "平多"), ("close:short", "平空")])
        self.order_type = combo([("market", "市价"), ("limit", "限价")])
        self.margin = combo([("cross", "全仓"), ("isolated", "逐仓")])
        self.size = QLineEdit()
        self.size.setPlaceholderText("合约张数，不是币数量")
        self.price, self.sl, self.tp = QLineEdit(), QLineEdit(), QLineEdit()
        self.reason, self.tags = QLineEdit(), QLineEdit()
        self.reason.setPlaceholderText("可选，保存供复盘使用")
        self.leverage = QLabel("待查询")
        for title, widget in (("合约", self.inst), ("操作", self.action), ("保证金", self.margin), ("订单类型", self.order_type),
                              ("数量(张)", self.size), ("限价", self.price), ("止损触发价", self.sl), ("止盈触发价", self.tp),
                              ("实际杠杆", self.leverage), ("交易理由", self.reason), ("标签", self.tags)):
            form.addRow(title, widget)
        self.order_type.currentIndexChanged.connect(lambda: self.price.setEnabled(self.order_type.currentData() == "limit"))
        self.price.setEnabled(False)
        self.margin.currentIndexChanged.connect(self.refresh_leverage)
        self.action.currentIndexChanged.connect(self.refresh_leverage)
        form_layout.addLayout(form)
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
        sidebar.setMaximumWidth(380)
        self.sidebar = sidebar
        splitter.addWidget(sidebar)
        splitter.setStretchFactor(0, 1)
        splitter.restore([850, 320])
        self.chart.maximize_requested.connect(self.maximize_chart)
        self.chart.bottom_requested.connect(lambda: self.toggle_area("bottom"))
        self.chart.sidebar_requested.connect(lambda: self.toggle_area("sidebar"))
        visibility = service.store.get("chart_layout", "trade_visibility", {"bottom": True, "sidebar": True})
        self.bottom.setVisible(visibility["bottom"])
        self.sidebar.setVisible(visibility["sidebar"])
        root.addWidget(splitter)

    def maximize_chart(self, maximized):
        if maximized:
            self.chart_visibility = (not self.bottom.isHidden(), not self.sidebar.isHidden(), not self.ai_panel.isHidden())
            self.bottom.hide()
            self.sidebar.hide()
            self.ai_panel.hide()
        else:
            bottom, sidebar, ai = self.chart_visibility
            self.bottom.setVisible(bottom)
            self.sidebar.setVisible(sidebar)
            self.ai_panel.setVisible(ai)

    def toggle_area(self, area):
        if self.chart.maximized:
            self.chart.maximize()
        widget = self.bottom if area == "bottom" else self.sidebar
        widget.setVisible(widget.isHidden())
        self.service.store.put("chart_layout", "trade_visibility", {"bottom": not self.bottom.isHidden(), "sidebar": not self.sidebar.isHidden()})

    def draft(self):
        action, direction = self.action.currentData().split(":")
        return OrderDraft(instrument_id(self.inst.text()), action, direction, self.order_type.currentData(), self.size.text().strip(),
                          self.price.text().strip(), self.margin.currentData(), self.sl.text().strip(), self.tp.text().strip(),
                          self.reason.text().strip(), self.tags.text().strip(), self.source)

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
            self.feedback.setText(str(exc))

    def load_draft(self, draft):
        self.source = draft.source
        self.inst.setText(draft.instrument)
        select_data(self.action, draft.action + ":" + draft.direction)
        select_data(self.order_type, draft.order_type)
        select_data(self.margin, draft.margin)
        for widget, value in ((self.size, draft.size), (self.price, draft.price), (self.sl, draft.stop_loss), (self.tp, draft.take_profit), (self.reason, draft.reason), (self.tags, draft.tags)):
            widget.setText(value)
        self.select_instrument()
        self.feedback.setText("草稿已载入，请检查合约张数、价格与方向，再确认提交。")

    def submit(self):
        token = None
        try:
            draft = self.draft()
            self.refresh_leverage()
            lever = self.current_leverage()
            if not lever:
                raise ValueError("尚未获取当前保证金模式的实际杠杆，请等待查询完成")
            context = self.ai_context()
            if self.ai_panel.last_record:
                context["analysis"] = self.ai_panel.last_record
            token, payload = self.service.trading.prepare(draft, context)
            env = "模拟交易" if self.service.environment == "demo" else "真实交易"
            summary = f"{env} · {draft.instrument}\n{self.action.currentText()} / {self.margin.currentText()} / {self.order_type.currentText()}\n数量：{draft.size} 张\n价格：{draft.price if draft.order_type == 'limit' else '按市场成交'}\n实际杠杆：{lever} 倍\n止损：{draft.stop_loss or '未设置'}  止盈：{draft.take_profit or '未设置'}"
            if not confirm(self, "确认提交订单", summary):
                self.service.trading.prepared.pop(token, None)
                return
            client_id = self.service.trading.submit(token, confirmed=True)
            self.last_submission_id = client_id
            self.feedback.setText("已提交，正在等待交易所确认：" + client_id)
            self.source = "manual"
        except (ValueError, OSError) as exc:
            if token:
                self.service.trading.prepared.pop(token, None)
            self.feedback.setText(str(exc))

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
            self.feedback.setText("已填入全部可平张数；可修改为部分平仓，再检查并确认。")
        except ValueError as exc:
            self.feedback.setText(str(exc))

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
        if not self.service.api.credentials:
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
        value, accepted = QInputDialog.getInt(self, "调整交易所杠杆", "新的杠杆倍数（交易所最终校验范围）", int(number(self.current_leverage() or "1")), 1, 125)
        if not accepted:
            return
        try:
            payload = {"instId": instrument_id(self.inst.text()), "mgnMode": self.margin.currentData(), "lever": str(value)}
            if self.service.account.get("posMode") == "long_short_mode" and payload["mgnMode"] == "isolated":
                payload["posSide"] = self.action.currentData().split(":")[1]
            if confirm(self, "确认修改杠杆", f"{payload['instId']} / {self.margin.currentText()}\n将交易所杠杆修改为 {value} 倍。"):
                self.perform("/api/v5/account/set-leverage", payload)
                self.service.leverages.clear()
        except ValueError as exc:
            self.feedback.setText(str(exc))

    def perform(self, path, payload):
        if self.action_busy:
            raise ValueError("上一操作仍在等待结果")
        self.action_busy = True
        def done(rows, error):
            self.action_busy = False
            self.feedback.setText((str(error) + ("；结果待确认，请刷新交易所状态，不要重复提交。" if error.uncertain else "")) if error else "交易所已接受操作，等待状态同步。")
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
            if confirm(self, "确认撤销订单", f"{row['instId']}\n订单 {identity}\n" + ("将撤销这笔止盈止损保护。" if is_algo else "撤单结果以交易所为准。")):
                payload = {"instId": row["instId"], key: identity}
                self.perform("/api/v5/trade/cancel-algos" if is_algo else "/api/v5/trade/cancel-order", [payload] if is_algo else payload)
        except ValueError as exc:
            self.feedback.setText(str(exc))

    def protect_position(self):
        try:
            position = self.selected_position()
            dialog = QDialog(self)
            dialog.setWindowTitle("为所选仓位设置止盈止损")
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
            if confirm(self, "确认设置交易所止盈止损", f"{inst} · {size.text()} 张\n止损 {sl.text() or '无'} / 止盈 {tp.text() or '无'}"):
                self.perform("/api/v5/trade/order-algo", payload)
        except (ValueError, KeyError) as exc:
            self.feedback.setText(str(exc))

    def refresh(self, kind):
        s = self.service
        if kind == "environment":
            self.action_busy = False
            self.last_submission_id = None
            self.leverage_pending.clear()
            self.source = "manual"
            for edit in (self.size, self.price, self.sl, self.tp, self.reason, self.tags):
                edit.clear()
        if kind == "selection":
            self.inst.setText(s.selected)
            self.refresh_leverage()
        if kind in ("candles", "selection", "market"):
            self.chart.set_data(s.candles.get((s.selected, s.bar), []), s.selected + " / " + s.bar)
            spec = s.specs.get(s.selected, {})
            self.spec_label.setText(f"每张 {spec.get('ctVal', '—')} {spec.get('ctValCcy', '')}\n最小张数 {spec.get('minSz', '—')} · 数量步长 {spec.get('lotSz', '—')}\n价格步长 {spec.get('tickSz', '—')}")
        if kind in ("account", "environment"):
            self.refresh_leverage()
            fill_table(self.position_table, [(p.get("posId"), [p["instId"], p["posSide"]+" / "+p["mgnMode"], p["pos"]+" / "+(p.get("availPos") or p["pos"]), p.get("avgPx"), p.get("upl"), p.get("lever")]) for p in s.positions])
            fill_table(self.order_table, [(o["ordId"], [o["instId"], o.get("side", "")+" / "+o.get("ordType", ""), o.get("sz"), o.get("px"), STATES.get(o.get("state"), o.get("state"))]) for o in s.pending_orders])
            fill_table(self.algo_table, [(o["algoId"], [o["instId"], o.get("side"), o.get("sz"), o.get("slTriggerPx"), o.get("tpTriggerPx"), o.get("state")]) for o in s.algos])
        if kind in ("orders", "history", "environment", "account"):
            if self.last_submission_id:
                recent = s.store.get("local_order", self.last_submission_id, scope=s.scope)
                if recent:
                    self.feedback.setText(STATES.get(recent["status"], recent["status"]) + " · " + (recent.get("error") or recent.get("protection", "")))
            records = [(key, [timestamp(o["time"]), o["draft"]["instrument"], o["draft"]["action"]+" / "+o["draft"]["direction"], STATES.get(o["status"], o["status"]), o.get("error") or o.get("protection", "")]) for key, o in s.store.list("local_order", s.scope, limit=100)]
            names = {"order-algo": "设置止盈止损", "cancel-order": "撤单", "cancel-algos": "撤销保护", "set-leverage": "修改杠杆"}
            for key, action in s.store.list("action", s.scope, limit=100):
                payload = action["payload"][0] if isinstance(action["payload"], list) else action["payload"]
                records.append(("action:" + key, [timestamp(action["time"]), payload.get("instId", ""), names.get(action["path"].rsplit("/", 1)[-1], "操作"), STATES.get(action["status"], action["status"]), action.get("error", "")]))
            fill_table(self.local_table, records)
            fill_table(self.fill_table, [(key, [timestamp(int(o.get("fillTime") or o["ts"])/1000), o["instId"], o["side"], o["fillSz"], o["fillPx"], (o.get("fee") or "0")+" "+o.get("feeCcy", "")]) for key, o in s.store.list("fills", s.scope, limit=100)])
