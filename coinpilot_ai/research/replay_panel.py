"""Bar Replay 与独立训练账户。"""
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QDateTime, QTimer
from PyQt6.QtWidgets import (QComboBox, QDateTimeEdit, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from coinpilot_ai.charts.canvas import CandleChart
from coinpilot_ai.market.intervals import BARS
from coinpilot_ai.trading.models import instrument_id, number
from coinpilot_ai.market.cache import HistoryLoader, MarketCache
from .replay import ReplaySession
from coinpilot_ai.ui.common import button, confirm, fill_table, selected_id, table
from coinpilot_ai.review.equity_curve import EquityCurve
from coinpilot_ai.charts.indicator_strip import IndicatorStrip
from coinpilot_ai.charts.settings import IndicatorSettingsDialog
from coinpilot_ai.review.panel import edit_journal_note
from coinpilot_ai.review.journal import aggregate
from coinpilot_ai.review.statistics import grouped_statistics, statistics


class ReplayPage(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.cache = MarketCache(Path(service.store.path).with_suffix(".market.sqlite3"))
        self.loader = HistoryLoader(service, self.cache, self)
        self.loader.finished.connect(self.loaded)
        self.session = None
        self.training_trades = []
        self.pending = None
        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.saved = QComboBox()
        self.reload_sessions()
        controls.addWidget(self.saved)
        self.symbol = QLineEdit(service.selected)
        self.symbol.setFixedWidth(155)
        controls.addWidget(self.symbol)
        self.bar = QComboBox()
        for bar in BARS:
            self.bar.addItem(bar, bar)
        self.bar.setCurrentIndex(self.bar.findData("15m"))
        controls.addWidget(self.bar)
        self.begin = QDateTimeEdit(QDateTime.currentDateTime().addDays(-7))
        self.begin.setCalendarPopup(True)
        self.begin.setDisplayFormat("yyyy-MM-dd HH:mm")
        controls.addWidget(self.begin)
        self.count = QSpinBox()
        self.count.setRange(20, 2000)
        self.count.setValue(200)
        controls.addWidget(self.count)
        self.fee = QLineEdit(str(service.settings.get("paper_fee", "0.0005")))
        self.fee.setPlaceholderText("手续费率")
        self.fee.setFixedWidth(90)
        controls.addWidget(self.fee)
        self.slippage = QLineEdit(str(service.settings.get("paper_slippage_bps", "0")))
        self.slippage.setPlaceholderText("滑点基点")
        self.slippage.setFixedWidth(85)
        controls.addWidget(self.slippage)
        controls.addWidget(button("加载回放", self.load))
        controls.addWidget(button("指标", self.edit_indicators))
        root.addLayout(controls)
        self.status = QLabel("选择合约、周期和起点，加载可用的历史 K 线。")
        root.addWidget(self.status)
        playback = QHBoxLayout()
        playback.addWidget(button("◀ 暂停", self.pause))
        playback.addWidget(button("▶ 下一根", self.step))
        playback.addWidget(button("▶ 连续", self.play))
        self.speed = QComboBox()
        for value in (1, 2, 5, 10):
            self.speed.addItem(f"{value}×", value)
        playback.addWidget(self.speed)
        playback.addStretch()
        root.addLayout(playback)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step)
        self.chart = CandleChart(service)
        root.addWidget(self.chart, 1)
        self.indicator_scroll = QScrollArea()
        self.indicator_scroll.setWidgetResizable(True)
        self.indicator_scroll.setMaximumHeight(150)
        self.indicator_host = QWidget()
        self.indicator_layout = QVBoxLayout(self.indicator_host)
        self.indicator_layout.setContentsMargins(0, 0, 0, 0)
        self.indicator_scroll.setWidget(self.indicator_host)
        root.addWidget(self.indicator_scroll)
        self.indicator_strips = []
        self.rebuild_indicators()
        self.chart.view_changed.connect(self.update_indicators)
        service.chart_book.changed.connect(self.indicator_changed)
        trade = QHBoxLayout()
        self.side = QComboBox()
        self.side.addItem("开多", "buy")
        self.side.addItem("开空", "sell")
        trade.addWidget(self.side)
        self.order_type = QComboBox()
        self.order_type.addItem("市价", "market")
        self.order_type.addItem("限价", "limit")
        self.order_type.addItem("Stop", "stop")
        self.order_type.addItem("Stop Limit", "stop_limit")
        self.order_type.addItem("Trailing Stop", "trailing_stop")
        trade.addWidget(self.order_type)
        self.quantity = QLineEdit()
        self.quantity.setPlaceholderText("张数")
        trade.addWidget(self.quantity)
        self.limit = QLineEdit()
        self.limit.setPlaceholderText("限价（可选）")
        trade.addWidget(self.limit)
        self.trigger = QLineEdit()
        self.trigger.setPlaceholderText("Stop 触发价")
        trade.addWidget(self.trigger)
        self.trail = QLineEdit()
        self.trail.setPlaceholderText("追踪距离")
        trade.addWidget(self.trail)
        self.stop = QLineEdit()
        self.stop.setPlaceholderText("止损（可选）")
        trade.addWidget(self.stop)
        self.target = QLineEdit()
        self.target.setPlaceholderText("止盈（可选）")
        trade.addWidget(self.target)
        trade.addWidget(button("加入下一根撮合", self.submit))
        root.addLayout(trade)
        self.orders = table(["时间", "方向", "张数", "价格", "费用", "盈亏"], readable=True)
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setMaximumHeight(190)
        self.detail_tabs.addTab(self.orders, "成交")
        self.pending_orders = table(["订单号", "方向", "类型", "张数", "状态"], readable=True)
        self.detail_tabs.addTab(self.pending_orders, "挂单")
        self.positions = table(["合约", "方向", "张数", "均价", "浮盈亏"], readable=True)
        self.detail_tabs.addTab(self.positions, "持仓")
        self.alerts = table(["训练时间", "提醒", "合约"], readable=True)
        self.detail_tabs.addTab(self.alerts, "训练提醒")
        journal_page = QWidget()
        journal_layout = QVBoxLayout(journal_page)
        journal_layout.setContentsMargins(3, 3, 3, 3)
        journal_controls = QHBoxLayout()
        self.stats_group = QComboBox()
        for value, title in (("all", "全部"), ("direction", "按方向"), ("instrument", "按合约"), ("tag", "按标签")):
            self.stats_group.addItem(title, value)
        self.stats_group.currentIndexChanged.connect(self.refresh_training_stats)
        journal_controls.addWidget(self.stats_group)
        self.stats = QLabel("暂无训练交易")
        journal_controls.addWidget(self.stats, 1)
        journal_layout.addLayout(journal_controls)
        self.journal = table(["开仓", "方向", "状态", "净额", "标签"], readable=True)
        self.journal.setToolTip("双击交易行编辑理由、错误、标签和初始风险")
        self.journal.itemDoubleClicked.connect(self.edit_training_note)
        journal_layout.addWidget(self.journal)
        self.detail_tabs.addTab(journal_page, "交易日志")
        root.addWidget(self.detail_tabs)
        root.addWidget(QLabel("资金费：未模拟资金费；训练行情仅含 K 线，不含对应结算时点的确认费率。"))
        self.equity_curve = EquityCurve(service.store, "replay:unselected")
        root.addWidget(self.equity_curve)

    def reload_sessions(self):
        self.saved.clear()
        self.saved.addItem("新建训练账户", None)
        for key, record in self.service.store.list("replay_session", limit=100):
            if record.get("version") == 1:
                self.saved.addItem(f"{record['instrument']} {record['bar']} · {key[:8]}", key)

    def load(self):
        self.pause()
        try:
            session_id = self.saved.currentData()
            record = self.service.store.get("replay_session", session_id) if session_id else None
            fee = number(record.get("fee", self.fee.text()) if record else self.fee.text(), "训练手续费率")
            slippage = number(record.get("slippage_bps", self.slippage.text()) if record else self.slippage.text(), "训练滑点")
            if not 0 <= fee <= 1 or not 0 <= slippage < 10000:
                raise ValueError("训练手续费率须在 0—1，滑点须低于 10000 基点")
            inst = record["instrument"] if record else instrument_id(self.symbol.text())
            bar = record["bar"] if record else self.bar.currentData()
            begin = record["begin"] if record else self.begin.dateTime().toMSecsSinceEpoch()
            end = record["end"] if record else begin + (self.count.value()-1)*BARS[bar]*1000
            if inst not in self.service.specs:
                raise ValueError("尚未获取该合约规格；请等待公共行情同步")
            self.pending = (inst, bar, begin, end, session_id, str(fee), str(slippage))
            self.status.setText("正在下载或读取历史 K 线…")
            self.loader.load(inst, bar, begin, end)
        except (ValueError, KeyError) as exc:
            self.status.setText(str(exc))

    def loaded(self, rows, error):
        if not self.pending:
            return
        inst, bar, _, _, session_id, fee, slippage = self.pending
        self.pending = None
        if error:
            self.status.setText(str(error))
            return
        try:
            self.session = ReplaySession(self.service.store, self.service.specs, inst, bar, rows,
                                         session_id, fee=fee, slippage_bps=slippage)
            self.equity_curve.scope = self.session.scope
            self.chart.set_context("replay", inst, bar)
            self.session.evaluate_rules(self.service.rules())
            self.refresh()
            self.reload_sessions()
            self.saved.setCurrentIndex(max(0, self.saved.findData(self.session.id)))
        except ValueError as exc:
            self.status.setText(str(exc))

    def refresh(self):
        session = self.session
        if not session:
            return
        self.chart.set_data(session.visible(), f"训练回放 · {session.instrument} · {session.bar}")
        self.status.setText(f"{session.cursor+1}/{len(session.rows)} 根 · 训练账户 {session.id[:8]} · "
                            f"虚拟权益 {session.broker.snapshot()[1]['totalEq']} USDT")
        fills = [row for _, row in self.service.store.list("fills", session.scope)]
        fill_table(self.orders, [(row["tradeId"], [row["fillTime"], row["side"], row["fillSz"],
                                                  row["fillPx"], row["fee"], row["fillPnl"]]) for row in fills])
        fill_table(self.pending_orders, [(key, [key, row.get("side", ""), row.get("ordType", ""),
                                               row.get("sz", ""), row.get("state", "")])
                                         for key, row in session.broker.state["pending"].items()])
        positions, _ = session.broker.snapshot()
        fill_table(self.positions, [(row["posId"], [row["instId"], "多" if number(row["pos"]) > 0 else "空",
                                                  row["pos"], row["avgPx"], row["upl"]])
                                    for row in positions])
        fill_table(self.alerts, [(key, [str(row["time"]), row["name"], row["instrument"]])
                                 for key, row in self.service.store.list("replay_alert", session.scope, limit=20)])
        orders = [row for _, row in self.service.store.list("orders", session.scope)]
        trades, _ = aggregate(fills, orders=orders)
        for trade in trades:
            trade["note"] = self.service.store.get("journal_note", trade["id"], {}, session.scope)
        self.training_trades = trades
        fill_table(self.journal, [(row["id"], [str(row["start"]), row["direction"], row["status"],
                                             str(row["net"]), ", ".join(row["note"].get("tags", []))])
                                  for row in trades])
        self.refresh_training_stats()
        self.update_indicators()
        self.equity_curve.update()

    def refresh_training_stats(self, *_):
        group = self.stats_group.currentData()
        values = (grouped_statistics(self.training_trades, group) if group != "all" else
                  {"全部": statistics(self.training_trades)})
        parts = []
        for name, row in values.items():
            parts.append(f"{name}: {row['trades']} 笔 · 胜率 " +
                         (f"{row['win_rate']:.1%}" if row["win_rate"] is not None else "—") +
                         f" · PF {row['profit_factor'] if row['profit_factor'] is not None else '—'}" +
                         f" · 平均 R {row['average_r'] if row['average_r'] is not None else '—'}" +
                         f" · 最大回撤 {row['max_drawdown']}")
        self.stats.setText("  |  ".join(parts) if parts else "暂无完整已平仓训练交易")

    def edit_training_note(self, *_):
        key = selected_id(self.journal)
        trade = next((row for row in self.training_trades if row["id"] == key), None)
        if trade and self.session and edit_journal_note(self.service.store, self.session.scope, trade, self):
            self.refresh()

    def step(self):
        if not self.session or not self.session.step():
            self.pause()
            return
        self.session.evaluate_rules(self.service.rules())
        self.refresh()

    def edit_indicators(self):
        IndicatorSettingsDialog(self.service.chart_book, self).exec()

    def indicator_changed(self, kind, _key):
        if kind == "indicators":
            self.rebuild_indicators()
            self.update_indicators()

    def rebuild_indicators(self):
        while self.indicator_layout.count():
            child = self.indicator_layout.takeAt(0).widget()
            if child is not None:
                child.deleteLater()
        self.indicator_strips = []
        for setting in self.service.chart_book.indicators:
            if setting["visible"] and setting["kind"] not in ("MA", "EMA", "BOLL", "VWAP", "SUPERTREND"):
                strip = IndicatorStrip(self.chart, setting["id"], self.indicator_host)
                self.indicator_layout.addWidget(strip)
                self.indicator_strips.append(strip)
        self.indicator_scroll.setVisible(bool(self.indicator_strips))

    def update_indicators(self):
        for strip in self.indicator_strips:
            strip.update()

    def play(self):
        if self.session:
            self.timer.start(max(50, 800//self.speed.currentData()))

    def pause(self):
        self.timer.stop()

    def submit(self):
        if not self.session:
            self.status.setText("请先加载回放")
            return
        side, kind = self.side.currentData(), self.order_type.currentData()
        payload = {"instId": self.session.instrument, "tdMode": "cross", "side": side,
                   "ordType": kind, "sz": self.quantity.text().strip(), "clOrdId": "replay-"+uuid4().hex[:24]}
        if kind in ("limit", "stop_limit"):
            payload["px"] = self.limit.text().strip()
        if kind in ("stop", "stop_limit"):
            payload["triggerPx"] = self.trigger.text().strip()
        if kind == "trailing_stop":
            payload["trailOffset"] = self.trail.text().strip()
        protection = {}
        for field, value in (("sl", self.stop.text().strip()), ("tp", self.target.text().strip())):
            if value:
                protection[field+"TriggerPx"] = value
        if protection:
            payload["attachAlgoOrds"] = [protection]
        if not confirm(self, "确认训练订单", f"{self.session.instrument} · {self.side.currentText()} · {kind} · {payload['sz']} 张\n下一根 K 线才进入撮合"):
            return
        try:
            self.session.submit(payload)
            self.status.setText("训练订单已排队，下一根 K 线进入撮合。")
        except (ValueError, KeyError) as exc:
            self.status.setText(str(exc))

    def shutdown(self):
        self.pause()
        self.loader.cancel()
        self.cache.close()
