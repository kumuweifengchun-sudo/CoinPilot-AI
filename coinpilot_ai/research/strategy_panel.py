"""可视化指标策略编辑与确定性回测。"""
from pathlib import Path

from PyQt6.QtCore import QDateTime
from PyQt6.QtWidgets import (QComboBox, QDateTimeEdit, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QVBoxLayout, QWidget)

from .backtest import run_backtest, validate_strategy
from coinpilot_ai.market.intervals import BARS
from coinpilot_ai.trading.models import instrument_id
from coinpilot_ai.market.cache import HistoryLoader, MarketCache
from coinpilot_ai.review.equity_curve import EquityCurve
from coinpilot_ai.ui.common import button, fill_table, table
from coinpilot_ai.trading.dialogs import IndicatorConditionDialog


class StrategyPage(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.cache = MarketCache(Path(service.store.path).with_suffix(".market.sqlite3"))
        self.loader = HistoryLoader(service, self.cache, self)
        self.loader.finished.connect(self.loaded)
        self.conditions = []
        self.pending = None
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.saved = QComboBox()
        self.saved.currentIndexChanged.connect(self.load_strategy)
        self.reload_strategies()
        self.name = QLineEdit("新策略")
        self.instrument = QLineEdit(service.selected)
        self.bar = QComboBox()
        for name in BARS:
            self.bar.addItem(name, name)
        self.bar.setCurrentIndex(self.bar.findData("15m"))
        self.direction = QComboBox()
        self.direction.addItem("做多", "long")
        self.direction.addItem("做空", "short")
        self.stop = QLineEdit("1")
        self.target = QLineEdit("2")
        self.risk = QLineEdit("1")
        self.fee = QLineEdit("0.0005")
        self.slippage = QLineEdit("0")
        self.initial = QLineEdit("10000")
        for title, widget in (("已有策略", self.saved), ("名称", self.name), ("合约", self.instrument),
                              ("周期", self.bar), ("方向", self.direction), ("止损 %", self.stop),
                              ("止盈 %", self.target), ("单笔风险 %", self.risk),
                              ("手续费率", self.fee), ("滑点 bps", self.slippage), ("初始资金", self.initial)):
            form.addRow(title, widget)
        root.addLayout(form)
        self.condition_list = QListWidget()
        self.condition_list.setMaximumHeight(80)
        root.addWidget(self.condition_list)
        actions = QHBoxLayout()
        for title, callback in (("添加指标条件", self.add_condition), ("删除所选", self.remove_condition),
                                ("保存策略", self.save_strategy)):
            actions.addWidget(button(title, callback))
        root.addLayout(actions)
        range_controls = QHBoxLayout()
        self.begin = QDateTimeEdit(QDateTime.currentDateTime().addDays(-30))
        self.end = QDateTimeEdit(QDateTime.currentDateTime().addDays(-7))
        self.begin.setCalendarPopup(True)
        self.end.setCalendarPopup(True)
        range_controls.addWidget(self.begin)
        range_controls.addWidget(self.end)
        range_controls.addWidget(button("下载历史并回测", self.run))
        root.addLayout(range_controls)
        self.status = QLabel("先设置指标入场条件，再选择历史范围。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.equity_curve = EquityCurve(service.store, "backtest:preview")
        root.addWidget(self.equity_curve)
        self.trades = table(["入场", "出场", "方向", "张数", "净额", "原因"], readable=True)
        root.addWidget(self.trades, 1)

    def reload_strategies(self):
        self.saved.blockSignals(True)
        self.saved.clear()
        self.saved.addItem("新策略", None)
        for key, row in self.service.store.list("strategy"):
            if row.get("version") == 1:
                self.saved.addItem(key, key)
        self.saved.blockSignals(False)

    def load_strategy(self, *_):
        key = self.saved.currentData()
        if not key:
            return
        record = self.service.store.get("strategy", key)
        if not record:
            return
        self.name.setText(key)
        self.instrument.setText(record["instrument"])
        self.bar.setCurrentIndex(self.bar.findData(record["bar"]))
        self.direction.setCurrentIndex(self.direction.findData(record["direction"]))
        self.stop.setText(str(record["stop_percent"]))
        self.target.setText(str(record["target_percent"]))
        self.risk.setText(str(record["risk_percent"]))
        self.conditions = list(record["conditions"])
        self.fee.setText(str(record.get("fee_rate", self.fee.text())))
        self.slippage.setText(str(record.get("slippage_bps", self.slippage.text())))
        self.initial.setText(str(record.get("initial", self.initial.text())))
        self.refresh_conditions()

    def refresh_conditions(self):
        self.condition_list.clear()
        for condition in self.conditions:
            self.condition_list.addItem(f"{condition['indicator']['kind']} {condition.get('line', 'value')} "
                                        f"{condition['op']} {condition.get('threshold', condition.get('compare', {}).get('indicator', {}).get('kind'))}")

    def add_condition(self):
        dialog = IndicatorConditionDialog(parent=self)
        if dialog.exec():
            self.conditions.append(dialog.condition)
            self.refresh_conditions()

    def remove_condition(self):
        index = self.condition_list.currentRow()
        if index >= 0:
            self.conditions.pop(index)
            self.refresh_conditions()

    def strategy(self):
        return validate_strategy({"version": 1, "name": self.name.text().strip(),
            "instrument": instrument_id(self.instrument.text()), "bar": self.bar.currentData(),
            "direction": self.direction.currentData(), "stop_percent": self.stop.text().strip(),
            "target_percent": self.target.text().strip(), "risk_percent": self.risk.text().strip(),
            "conditions": list(self.conditions)})

    def save_strategy(self):
        try:
            strategy = self.strategy()
            if not strategy["name"]:
                raise ValueError("请填写策略名称")
            strategy.update(fee_rate=self.fee.text().strip(),
                            slippage_bps=self.slippage.text().strip(), initial=self.initial.text().strip())
            self.service.store.put("strategy", strategy["name"], strategy)
            self.reload_strategies()
            self.saved.setCurrentIndex(max(0, self.saved.findData(strategy["name"])))
            self.status.setText("策略已保存")
        except (ValueError, KeyError) as exc:
            self.status.setText(str(exc))

    def run(self):
        try:
            strategy = self.strategy()
            inst, bar = strategy["instrument"], strategy["bar"]
            if inst not in self.service.specs:
                raise ValueError("尚未获取合约规格")
            begin, end = self.begin.dateTime().toMSecsSinceEpoch(), self.end.dateTime().toMSecsSinceEpoch()
            if begin >= end:
                raise ValueError("结束时间必须晚于起始时间")
            self.pending = strategy
            self.status.setText("正在读取历史数据…")
            self.loader.load(inst, bar, begin, end)
        except (ValueError, KeyError) as exc:
            self.status.setText(str(exc))

    def loaded(self, rows, error):
        if self.pending is None:
            return
        strategy, self.pending = self.pending, None
        if error:
            self.status.setText(str(error))
            return
        try:
            result = run_backtest(rows, strategy, self.service.specs[strategy["instrument"]],
                                  initial=self.initial.text(), fee_rate=self.fee.text(), slippage_bps=self.slippage.text())
            result["coverage"]["sources"] = self.cache.coverage(strategy["instrument"], strategy["bar"],
                int(rows[0][0]), int(rows[-1][0]))["segments"]
            self.service.store.append("backtest", {"version": 1, "strategy": strategy,
                "result": result, "coverage": result["coverage"], "assumptions": result["assumptions"]})
            self.equity_curve.set_points(result["equity_curve"])
            headline = (f"{result['coverage']['bars']} 根 K 线 · {len(result['trades'])} 笔交易 · "
                        f"已实现权益 {result['final_realized_equity']} · 最大回撤 {result['max_drawdown']}")
            if result["win_rate"] is not None:
                headline += f" · 胜率 {result['win_rate']:.1%}"
                headline += (f" · Profit Factor {result['profit_factor']:.2f}"
                             if result["profit_factor"] is not None else " · Profit Factor 无亏损交易")
            else:
                headline += " · 无已平仓交易"
            self.status.setText(headline)
            fill_table(self.trades, [(str(i), [str(row["entry_time"]), str(row["exit_time"]),
                row["direction"], str(row["size"]), str(row["net"]), row["cause"]])
                for i, row in enumerate(result["trades"])])
        except (ValueError, KeyError, TypeError) as exc:
            self.status.setText(str(exc))

    def shutdown(self):
        self.loader.cancel()
        self.cache.close()
