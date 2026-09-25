"""全市场扫描器界面与可保存筛选模板。"""
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from .scanner import MarketScanner, TEMPLATES
from coinpilot_ai.ui.common import button, fill_table, selected_id, table


class ScannerPanel(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.scanner = MarketScanner(service, self)
        root = QVBoxLayout(self)
        root.setContentsMargins(3, 3, 3, 3)
        self.templates = QComboBox()
        self.templates.currentIndexChanged.connect(self.load_template)
        root.addWidget(self.templates)
        self.filters = {}
        form = QFormLayout()
        for key, title in (("change_min", "15m 涨幅 ≥ %"), ("volume_min", "放量 ≥ 倍"),
                           ("rsi_min", "RSI ≥"), ("rsi_max", "RSI ≤"), ("atr_pct_min", "ATR ≥ %")):
            edit = QLineEdit()
            edit.setPlaceholderText("留空不筛选")
            self.filters[key] = edit
            form.addRow(title, edit)
        for key, title in (("breakout", "突破 24h 高点"), ("ema_cross", "EMA 金叉"), ("macd_cross", "MACD 金叉")):
            check = QCheckBox(title)
            self.filters[key] = check
            form.addRow(title, check)
        filter_host = QWidget()
        filter_host.setLayout(form)
        filter_scroll = QScrollArea()
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setMaximumHeight(200)
        filter_scroll.setWidget(filter_host)
        root.addWidget(filter_scroll)
        actions = QHBoxLayout()
        actions.addWidget(button("应用筛选", self.refresh))
        actions.addWidget(button("保存模板…", self.save_template))
        root.addLayout(actions)
        self.status = QLabel("打开扫描页后开始获取市场数据。")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.rows = table(["合约", "价格", "24h", "15m", "放量", "RSI", "状态"], readable=True)
        self.rows.itemDoubleClicked.connect(lambda _: self.open_symbol())
        root.addWidget(self.rows, 1)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(500)
        self.render_timer.timeout.connect(self.refresh)
        self.scanner.changed.connect(self.render_timer.start)
        self.reload_templates()

    def reload_templates(self):
        current = self.templates.currentData()
        self.templates.blockSignals(True)
        self.templates.clear()
        for name, filters in TEMPLATES.items():
            self.templates.addItem(name, filters)
        for name, record in self.service.store.list("scanner_template"):
            if record.get("version") == 1:
                self.templates.addItem(name, record["filters"])
        index = self.templates.findData(current)
        self.templates.setCurrentIndex(max(0, index))
        self.templates.blockSignals(False)
        self.load_template()

    def load_template(self, *_):
        filters = self.templates.currentData() or {}
        for key, widget in self.filters.items():
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(filters.get(key, False)))
            else:
                widget.setText(str(filters.get(key, "")))
        self.refresh()

    def current_filters(self):
        result = {}
        for key, widget in self.filters.items():
            if isinstance(widget, QCheckBox):
                if widget.isChecked():
                    result[key] = True
            elif widget.text().strip():
                value = float(widget.text().strip())
                if not 0 <= value <= 100000:
                    raise ValueError("筛选阈值必须在 0—100000 之间")
                result[key] = value
        return result

    def export_state(self):
        return {"version": 1, "template": self.templates.currentText(),
                "filters": {key: widget.isChecked() if isinstance(widget, QCheckBox) else widget.text()
                            for key, widget in self.filters.items()}}

    def apply_state(self, state):
        if not state or state.get("version") != 1:
            return
        self.reload_templates()
        index = self.templates.findText(state.get("template", ""))
        if index >= 0:
            self.templates.blockSignals(True)
            self.templates.setCurrentIndex(index)
            self.templates.blockSignals(False)
        for key, value in state.get("filters", {}).items():
            widget = self.filters.get(key)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif widget is not None:
                widget.setText(str(value))
        self.refresh()

    def save_template(self):
        try:
            filters = self.current_filters()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        name, accepted = QInputDialog.getText(self, "保存扫描模板", "模板名称")
        if accepted and name.strip():
            self.service.store.put("scanner_template", name.strip()[:40], {"version": 1, "filters": filters})
            self.reload_templates()

    def refresh(self):
        try:
            filters = self.current_filters()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        rows = self.scanner.results(filters)
        visible = rows[:200]
        fill_table(self.rows, [(inst, [inst, f"{ticker['price']:.7g}" if ticker else "—",
                                      f"{ticker['change24h']:+.2f}%" if ticker else "—",
                                      f"{metrics['change']:+.2f}%" if metrics else "—",
                                      f"{metrics['volume_ratio']:.2f}×" if metrics and metrics['volume_ratio'] is not None else "—",
                                      f"{metrics['rsi']:.1f}" if metrics and metrics['rsi'] is not None else "—", state])
                              for inst, ticker, metrics, state, _ in visible])
        matched = sum(row[4] is True for row in rows)
        self.status.setText(f"覆盖 {len(self.service.specs)} 个合约 · 匹配 {matched} · 待计算或不可用 {len(rows)-matched} · 最多显示 200 行")

    def open_symbol(self):
        inst = selected_id(self.rows)
        if inst:
            self.service.select(inst)

    def showEvent(self, event):
        super().showEvent(event)
        self.scanner.start()

    def hideEvent(self, event):
        self.scanner.stop()
        super().hideEvent(event)
