"""手动记账表单。与真实下单表单分离，共用项目主题及复盘入口。"""
from copy import deepcopy
from collections.abc import Callable
from dataclasses import asdict
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from PyQt6.QtCore import QDateTime, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea,
    QTextEdit, QVBoxLayout, QWidget)

from coinpilot_ai.ui.common import button, combo, confirm, fill_table, select_data, selected_id, show_text, table, timestamp
from coinpilot_ai.trading.models import number
from coinpilot_ai.charts.snapshot import CapturedChart
from .snapshots import ChartSnapshot, MAX_SNAPSHOTS
from .snapshot_widgets import SnapshotGallery, snapshot_label
from .manual import (AmountMode, EquitySnapshot, ManualCost, ManualFill, ManualLedger, ManualTrade,
                     calculate, integer, obj, opening_quantity, text)

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


def date_edit() -> QDateTimeEdit:
    edit = QDateTimeEdit(QDateTime.currentDateTime())
    edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
    edit.setCalendarPopup(True)
    return edit


def set_time(edit: QDateTimeEdit, value: int) -> None:
    edit.setDateTime(QDateTime.fromMSecsSinceEpoch(value))


class AmountEditor(QWidget):
    """金额方式切换保持同一数量；无价格或杠杆时拒绝破坏原草稿。"""

    def __init__(self, parent: QWidget | None = None, *, closing: bool = False) -> None:
        super().__init__(parent)
        self.closing = closing
        self.product = "swap"
        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setContentsMargins(0, 0, 0, 0)
        self.when = date_edit()
        self.price = QLineEdit()
        self.mode = combo([("all", "全部"), ("percent", "剩余仓位比例 %"), ("base", "基础币数量")]
                          if closing else [("notional", "仓位金额 USDT"), ("margin", "保证金 × 杠杆"), ("base", "基础币数量（现货）")])
        self.amount, self.leverage = QLineEdit(), QLineEdit()
        self.amount_label = QLabel("数量／金额")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.previous_mode: AmountMode = "all" if closing else "notional"
        form.addRow("实际成交时间", self.when)
        form.addRow("实际成交价 USDT", self.price)
        form.addRow("数量输入", self.mode)
        form.addRow(self.amount_label, self.amount)
        if not closing:
            form.addRow("杠杆", self.leverage)
        form.addRow(self.hint)
        self.mode.currentIndexChanged.connect(self.change_mode)
        for edit in (self.price, self.amount, self.leverage):
            edit.textChanged.connect(self.preview)
        self.preview()

    def set_product(self, value: str) -> None:
        if self.product != value and not self.closing and self.amount.text().strip():
            try:
                quantity = opening_quantity(self.price.text(), self.previous_mode, self.amount.text(), self.leverage.text())
                self.amount.setText(str(quantity * number(self.price.text())))
            except ValueError:
                self.amount.clear()
            self.mode.blockSignals(True)
            select_data(self.mode, "notional")
            self.mode.blockSignals(False)
        self.product = value
        if not self.closing:
            old = self.mode.currentData()
            self.mode.blockSignals(True)
            self.mode.clear()
            choices = [("notional", "开仓仓位金额 USDT"), ("margin", "保证金 × 杠杆")] if value == "swap" else [("notional", "买入金额 USDT"), ("base", "基础币数量")]
            for key, label in choices:
                self.mode.addItem(label, key)
            select_data(self.mode, old if old in {key for key, _ in choices} else "notional")
            self.mode.blockSignals(False)
            self.leverage.setEnabled(value == "swap")
            if value == "spot":
                self.leverage.clear()
            self.previous_mode = self.mode.currentData()
        self.preview()

    def change_mode(self) -> None:
        mode: AmountMode = self.mode.currentData()
        if not self.closing and self.amount.text().strip() and mode != self.previous_mode:
            try:
                quantity = opening_quantity(self.price.text(), self.previous_mode, self.amount.text(), self.leverage.text())
                amount = quantity if mode == "base" else quantity * number(self.price.text(), positive=True)
                if mode == "margin":
                    amount /= number(self.leverage.text(), "切换前请填写杠杆", positive=True)
                self.amount.setText(str(amount))
            except ValueError as exc:
                self.mode.blockSignals(True)
                select_data(self.mode, self.previous_mode)
                self.mode.blockSignals(False)
                self.hint.setText(str(exc))
                return
        self.previous_mode = mode
        self.preview()

    def preview(self) -> None:
        mode = self.mode.currentData()
        self.amount.setEnabled(mode != "all")
        self.amount_label.setText({"margin": "初始保证金 USDT", "notional": "开仓名义金额 USDT",
                                   "base": "基础币数量", "percent": "平仓比例 %", "all": "关闭全部剩余仓位"}.get(mode, "数量"))
        if self.closing:
            self.hint.setText("按剩余币数量平仓；不使用开仓时的 USDT 金额。")
            return
        try:
            quantity = opening_quantity(self.price.text(), mode, self.amount.text(), self.leverage.text())
            notional = quantity * number(self.price.text())
            margin = notional / number(self.leverage.text(), positive=True) if self.leverage.text() else None
            self.hint.setText(f"基础币数量 {quantity:.10g} · 名义金额 {notional:.10g} USDT"
                              + (f"\n理论初始保证金 {margin:.10g} USDT" if margin is not None else "")
                              + "\n保证金不含手续费或后续追加资金；请与实际成交核对。")
        except ValueError:
            self.hint.setText("填写实际价格与金额后换算。未知数量不使用实时行情推算。")

    def value(self, original: ManualFill | None = None) -> ManualFill:
        fill = deepcopy(original) if original else ManualFill()
        fill.time = self.when.dateTime().toMSecsSinceEpoch()
        fill.action = "close" if self.closing else "open"
        fill.price = self.price.text().strip()
        fill.amount_mode = self.mode.currentData()
        fill.amount = self.amount.text().strip()
        fill.leverage = self.leverage.text().strip() if self.product == "swap" and not self.closing else ""
        return fill

    def load(self, fill: ManualFill) -> None:
        self.mode.blockSignals(True)
        select_data(self.mode, fill.amount_mode)
        self.previous_mode = fill.amount_mode
        self.mode.blockSignals(False)
        set_time(self.when, fill.time)
        self.price.setText(fill.price)
        self.amount.setText(fill.amount)
        self.leverage.setText(fill.leverage)
        self.preview()


class FillDialog(QDialog):
    def __init__(self, product: str, original: ManualFill | None, closing: bool, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("成交明细 · 平仓" if closing else "成交明细 · 开仓／加仓")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        self.editor = AmountEditor(closing=closing)
        self.editor.set_product(product)
        if original:
            self.editor.load(original)
        self.original = original
        layout.addWidget(self.editor)
        form = QFormLayout()
        self.reason = QLineEdit(original.reason if original else "")
        self.external = QLineEdit(original.external_id if original else "")
        form.addRow("本次操作原因", self.reason)
        form.addRow("外部成交号（可选）", self.external)
        layout.addLayout(form)
        self.feedback = QLabel()
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self) -> ManualFill:
        fill = self.editor.value(self.original)
        fill.reason, fill.external_id = self.reason.text().strip(), self.external.text().strip()
        return fill

    def check(self) -> None:
        try:
            fill = self.value()
            number(fill.price, "成交价", positive=True)
            if fill.action == "open":
                opening_quantity(fill.price, fill.amount_mode, fill.amount, fill.leverage)
            elif fill.amount_mode != "all":
                number(fill.amount, "平仓数量或比例", positive=True)
            self.accept()
        except ValueError as exc:
            self.feedback.setText(str(exc))


class ManualEntry(QWidget):
    saved = pyqtSignal(str, str)

    def __init__(self, service: "CockpitService", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.ledger = service.manual_ledger
        self.trade = ManualTrade()
        self.detail_fills: list[ManualFill] = []
        self.costs: list[ManualCost] = []
        self.chart_capture: Callable[[bool], list[CapturedChart]] | None = None
        self.snapshots: list[ChartSnapshot] = []
        self.snapshot_temp: TemporaryDirectory[str] | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(8)
        self.book = combo([])
        self.reload_books()
        books = QHBoxLayout()
        books.addWidget(self.book, 1)
        books.addWidget(button("新账本", self.new_book))
        layout.addLayout(books)
        form = QFormLayout()
        self.product = combo([("swap", "USDT 本位永续"), ("spot", "现货 · USDT")])
        self.symbol = QLineEdit("BTC-USDT")
        self.direction = combo([("long", "做多／买入"), ("short", "做空")])
        self.mode = combo([("summary", "快速简录"), ("detail", "逐次成交明细")])
        for label, widget in (("产品", self.product), ("交易对", self.symbol), ("方向", self.direction), ("录入方式", self.mode)):
            form.addRow(label, widget)
        layout.addLayout(form)
        self.summary = QWidget()
        summary_layout = QVBoxLayout(self.summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.opening = AmountEditor()
        self.opening.set_product("swap")
        self.closed = QCheckBox("已全部平仓")
        self.closing = AmountEditor(closing=True)
        self.closing.mode.setEnabled(False)
        self.closing.setVisible(False)
        self.closed.toggled.connect(self.closing.setVisible)
        summary_layout.addWidget(QLabel("开仓汇总（实际均价与总量）"))
        summary_layout.addWidget(self.opening)
        self.open_external = QLineEdit()
        self.open_external.setPlaceholderText("开仓外部成交号（可选；多次成交请用明细）")
        summary_layout.addWidget(self.open_external)
        summary_layout.addWidget(self.closed)
        summary_layout.addWidget(self.closing)
        self.close_external = QLineEdit()
        self.close_external.setPlaceholderText("平仓外部成交号（可选）")
        self.close_external.setVisible(False)
        self.closed.toggled.connect(self.close_external.setVisible)
        summary_layout.addWidget(self.close_external)
        layout.addWidget(self.summary)
        self.detail = QWidget()
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.fills_table = table(["时间", "操作", "价格", "数量方式 / 金额"])
        self.fills_table.setMinimumHeight(160)
        detail_layout.addWidget(self.fills_table)
        for label, handler in (("添加开仓／加仓", lambda: self.edit_fill(False)), ("添加减仓／平仓", lambda: self.edit_fill(True)),
                               ("编辑选中成交", self.edit_selected_fill), ("移除选中成交", self.remove_fill)):
            detail_layout.addWidget(button(label, handler))
        self.detail.setVisible(False)
        layout.addWidget(self.detail)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.product.currentIndexChanged.connect(self.product_changed)
        self.before_enabled = QCheckBox("记录开仓前账户总权益")
        self.after_enabled = QCheckBox("记录最终平仓后账户总权益")
        self.before_equity, self.after_equity = QLineEdit(), QLineEdit()
        self.before_time, self.after_time = date_edit(), date_edit()
        for enabled, edit, when in ((self.before_enabled, self.before_equity, self.before_time),
                                    (self.after_enabled, self.after_equity, self.after_time)):
            layout.addWidget(enabled)
            fields = QWidget()
            fields_form = QFormLayout(fields)
            fields_form.setContentsMargins(0, 0, 0, 0)
            fields_form.addRow("账户总权益 USDT", edit)
            fields_form.addRow("快照时间", when)
            fields.setVisible(False)
            enabled.toggled.connect(fields.setVisible)
            layout.addWidget(fields)
        hint = QLabel("账户总权益含其他交易与浮盈亏；不作为本笔盈亏。快照可留空后补。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.fees_known = QCheckBox("手续费已核对完整（无费用也需确认）")
        self.funding_known = QCheckBox("资金费已核对完整（无资金费也需确认）")
        layout.addWidget(self.fees_known)
        layout.addWidget(self.funding_known)
        self.costs_table = table(["类型", "原币金额", "折算 USDT", "关联成交"])
        self.costs_table.setMinimumHeight(120)
        layout.addWidget(self.costs_table)
        cost_actions = QHBoxLayout()
        cost_actions.addWidget(button("添加费用", self.add_cost))
        cost_actions.addWidget(button("移除费用", self.remove_cost))
        layout.addLayout(cost_actions)
        snapshot_group = QGroupBox("交易图表快照 · 支持多个周期、多张图片")
        snapshot_layout = QVBoxLayout(snapshot_group)
        self.snapshot_stage = combo([(v, v) for v in ("分析", "开仓", "持仓", "平仓", "复盘")])
        self.snapshot_note = QLineEdit()
        self.snapshot_note.setPlaceholderText("本组快照说明（可选）")
        snapshot_layout.addWidget(self.snapshot_stage)
        snapshot_layout.addWidget(self.snapshot_note)
        self.capture_current = button("截取当前图表", lambda: self.capture_snapshots(False))
        self.capture_all = button("截取当前多图", lambda: self.capture_snapshots(True))
        self.capture_current.setEnabled(False)
        self.capture_all.setEnabled(False)
        snapshot_layout.addWidget(self.capture_current)
        snapshot_layout.addWidget(self.capture_all)
        hint = QLabel("切换周期后可继续追加；多图模式逐张采集。保存交易后归档。仅记录采集时的画面，不补造过去现场。")
        hint.setWordWrap(True)
        snapshot_layout.addWidget(hint)
        self.snapshot_list = QListWidget()
        self.snapshot_list.setIconSize(QSize(64, 48))
        self.snapshot_list.setMinimumHeight(140)
        self.snapshot_list.setMaximumHeight(220)
        self.snapshot_list.itemDoubleClicked.connect(lambda _: self.view_snapshots())
        snapshot_layout.addWidget(self.snapshot_list)
        snapshot_layout.addWidget(button("查看图表快照", self.view_snapshots))
        snapshot_layout.addWidget(button("移除选中快照", self.remove_snapshot))
        layout.addWidget(snapshot_group)
        self.notes: dict[str, QTextEdit] = {}
        extra = QGroupBox("交易计划与事后复盘（展开填写）")
        extra.setCheckable(True)
        extra_layout = QVBoxLayout(extra)
        extra_body = QWidget()
        extra_form = QFormLayout(extra_body)
        for key, label in (("reason", "入场理由"), ("tags", "策略标签（逗号分隔）"), ("timeframe", "分析周期"),
                           ("margin_mode", "保证金模式（全仓／逐仓，可选）"),
                           ("planned_entry", "计划入场价"), ("initial_stop", "初始止损价"), ("target", "目标价"),
                           ("risk_budget", "初始风险 USDT"), ("exit_reason", "退出原因"), ("followed_plan", "是否遵守计划"),
                           ("mistake", "错误与改进"), ("memo", "补充笔记"), ("capital_note", "追加保证金／杠杆调整记录（不改变数量）")):
            edit = QTextEdit()
            edit.setMaximumHeight(64)
            self.notes[key] = edit
            extra_form.addRow(label, edit)
        self.attachments = QListWidget()
        self.attachments.itemDoubleClicked.connect(lambda item: self.view_attachment(item.text()))
        self.attachments.setMaximumHeight(100)
        extra_form.addRow("截图附件", self.attachments)
        extra_form.addRow(button("添加截图", self.add_attachment))
        extra_form.addRow(button("移除截图", lambda: self.attachments.takeItem(self.attachments.currentRow())))
        extra_layout.addWidget(extra_body)
        extra.toggled.connect(extra_body.setVisible)
        extra.setChecked(False)
        layout.addWidget(extra)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.feedback = QLabel("仅记账，不发送交易指令。未知费用不会当作零。")
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        actions = QHBoxLayout()
        actions.addWidget(button("新建", self.reset))
        actions.addWidget(button("检查并预览", self.preview))
        root.addLayout(actions)
        root.addWidget(button("保存交易记录", self.save, True))

    def reload_books(self, selected: str = "") -> None:
        current = selected or str(self.book.currentData() or "")
        self.book.clear()
        for book in self.ledger.books():
            self.book.addItem(f"{book['name']} · {book['exchange']} / {book['account']}", book["id"])
        select_data(self.book, current)

    def bind_chart_capture(self, provider: Callable[[bool], list[CapturedChart]]) -> None:
        self.chart_capture = provider
        self.capture_current.setEnabled(True)
        self.capture_all.setEnabled(True)

    def capture_snapshots(self, all_charts: bool) -> None:
        try:
            if self.chart_capture is None:
                raise ValueError("请从工作台图表旁的记账面板采集")
            captures = self.chart_capture(all_charts)
            if not captures or len(self.snapshots) + len(captures) > MAX_SNAPSHOTS:
                raise ValueError(f"每笔交易最多保存 {MAX_SNAPSHOTS} 张图表快照")
            if self.snapshot_temp is None:
                self.snapshot_temp = TemporaryDirectory(prefix="coinpilot-chart-snapshots-")
                self.destroyed.connect(lambda _=None, temp=self.snapshot_temp: temp.cleanup())
            snapshots: list[ChartSnapshot] = []
            from .manual import identity
            for captured in captures:
                key = identity()
                path = Path(self.snapshot_temp.name) / (key + ".png")
                if not captured.image.save(str(path), "PNG"):
                    raise ValueError("快照写入失败，请检查磁盘空间")
                snapshot = ChartSnapshot(key, str(path), hashlib.sha256(path.read_bytes()).hexdigest(),
                    captured.captured_at, captured.instrument, captured.period, captured.display_period,
                    str(self.snapshot_stage.currentData()), self.snapshot_note.text().strip(), captured.state_json)
                snapshots.append(ChartSnapshot.parse(asdict(snapshot)))
            self.snapshots.extend(snapshots)
            self.refresh_snapshots()
            self.feedback.setText(f"已追加 {len(snapshots)} 张图表快照，共 {len(self.snapshots)} 张；保存交易后归档。")
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))

    def refresh_snapshots(self) -> None:
        self.snapshot_list.clear()
        for snapshot in self.snapshots:
            item = QListWidgetItem(snapshot_label(snapshot))
            item.setToolTip(snapshot_label(snapshot))
            item.setIcon(QIcon(snapshot.image_path))
            self.snapshot_list.addItem(item)

    def view_snapshots(self) -> None:
        if self.snapshots:
            SnapshotGallery(self.snapshots, self, selected=max(0, self.snapshot_list.currentRow())).exec()

    def remove_snapshot(self) -> None:
        index = self.snapshot_list.currentRow()
        if index >= 0:
            self.snapshots.pop(index)
            self.refresh_snapshots()

    def clear_snapshot_draft(self) -> None:
        if self.snapshot_temp is not None:
            self.snapshot_temp.cleanup()
            self.snapshot_temp = None

    def new_book(self) -> None:
        if self.trade.version:
            self.feedback.setText("请先点击新建，再创建或选择另一账本；已保存记录不能移动账本。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("新建手动账本")
        form = QFormLayout(dialog)
        fields = [QLineEdit() for _ in range(3)]
        for label, edit in zip(("账本名称", "交易所", "账户别名（不填账号或密钥）"), fields):
            form.addRow(label, edit)
        feedback = QLabel()
        form.addRow(feedback)
        def save() -> None:
            try:
                key = self.ledger.create_book(*(edit.text() for edit in fields))
                self.reload_books(key)
                self.service.updated.emit("manual")
                dialog.accept()
            except ValueError as exc:
                feedback.setText(str(exc))
        form.addRow(button("创建", save, True))
        dialog.exec()

    def product_changed(self) -> None:
        product = self.product.currentData()
        self.opening.set_product(product)
        self.direction.setEnabled(product == "swap")
        if product == "spot":
            select_data(self.direction, "long")
        self.funding_known.setVisible(product == "swap")

    def mode_changed(self) -> None:
        detail = self.mode.currentData() == "detail"
        self.detail.setVisible(detail)
        self.summary.setVisible(not detail)
        if detail and self.trade.version and self.trade.mode == "summary":
            self.feedback.setText("转换明细：请录入真实逐次成交及费用，保存时核对原汇总；原版本会保留。")

    def refresh_tables(self) -> None:
        fill_table(self.fills_table, [(f.id, [timestamp(f.time / 1000), "开／加仓" if f.action == "open" else "减／平仓",
                                            f.price, f.amount_mode + " / " + f.amount]) for f in self.detail_fills])
        fill_table(self.costs_table, [(str(i), ["手续费" if c.kind == "fee" else "资金费", c.amount + " " + c.currency,
                                               c.amount if c.currency == "USDT" else c.usdt or "未知", c.fill_id or "整笔汇总"])
                                    for i, c in enumerate(self.costs)])

    def edit_fill(self, closing: bool, original: ManualFill | None = None) -> None:
        dialog = FillDialog(self.product.currentData(), original, closing, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            fill = dialog.value()
            if original:
                self.detail_fills[self.detail_fills.index(original)] = fill
            else:
                self.detail_fills.append(fill)
            self.detail_fills.sort(key=lambda f: f.time)
            self.refresh_tables()

    def edit_selected_fill(self) -> None:
        key = selected_id(self.fills_table)
        fill = next((f for f in self.detail_fills if f.id == key), None)
        if fill:
            self.edit_fill(fill.action == "close", fill)

    def remove_fill(self) -> None:
        key = selected_id(self.fills_table)
        self.detail_fills = [f for f in self.detail_fills if f.id != key]
        self.refresh_tables()

    def add_cost(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("费用：手续费正数为支出，资金费正数为收入")
        dialog.setMinimumWidth(480)
        form = QFormLayout(dialog)
        kind = combo([("fee", "手续费（负数为返佣）"), ("funding", "资金费（负数为支出）")])
        amount, currency, converted = QLineEdit(), QLineEdit("USDT"), QLineEdit()
        linked = combo([("", "整笔交易")])
        for fill in self.current_fills():
            linked.addItem(f"{timestamp(fill.time / 1000)} · {fill.action}", fill.id)
        for label, widget in (("类型", kind), ("金额", amount), ("币种", currency), ("折算 USDT（非 USDT 费用）", converted), ("关联成交（基础币扣费必选）", linked)):
            form.addRow(label, widget)
        feedback = QLabel()
        form.addRow(feedback)
        def save() -> None:
            try:
                number(amount.text(), "费用金额")
                if converted.text():
                    number(converted.text(), "折算金额")
                self.costs.append(ManualCost(kind.currentData(), amount.text().strip(), currency.text().strip().upper(),
                                             converted.text().strip(), str(linked.currentData())))
                self.refresh_tables()
                dialog.accept()
            except ValueError as exc:
                feedback.setText(str(exc))
        form.addRow(button("添加", save, True))
        dialog.exec()

    def remove_cost(self) -> None:
        key = selected_id(self.costs_table)
        if key is not None and str(key).isdigit():
            self.costs.pop(int(key))
            self.refresh_tables()

    def add_attachment(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择复盘截图", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        for path in paths:
            if QPixmap(path).isNull():
                self.feedback.setText("无法读取所选图片，请选择有效截图。")
            else:
                self.attachments.addItem(path)

    def view_attachment(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.feedback.setText("截图文件不可用。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("交易复盘截图")
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        area = QScrollArea()
        label = QLabel()
        label.setPixmap(pixmap)
        area.setWidget(label)
        layout.addWidget(area)
        dialog.exec()

    def current_fills(self) -> list[ManualFill]:
        if self.mode.currentData() == "detail":
            return deepcopy(self.detail_fills)
        # 草稿首次生成即保留稳定 ID，以便费用关联和重复保存。
        if not self.trade.fills:
            self.trade.fills = [ManualFill()]
        values = [self.opening.value(self.trade.fills[0])]
        values[0].external_id = self.open_external.text().strip()
        if self.closed.isChecked():
            if len(self.trade.fills) == 1:
                self.trade.fills.append(ManualFill(action="close", amount_mode="all"))
            values.append(self.closing.value(self.trade.fills[1]))
            values[-1].external_id = self.close_external.text().strip()
        return values

    def value(self) -> ManualTrade:
        trade = deepcopy(self.trade)
        trade.book_id = str(self.book.currentData() or "")
        trade.product, trade.direction, trade.mode = self.product.currentData(), self.direction.currentData(), self.mode.currentData()
        trade.symbol = self.symbol.text().strip().upper().replace("/", "-")
        trade.fills = self.current_fills()
        trade.costs = deepcopy(self.costs)
        trade.fees_known, trade.funding_known = self.fees_known.isChecked(), self.funding_known.isChecked()
        trade.before = EquitySnapshot(time=self.before_time.dateTime().toMSecsSinceEpoch(), equity=self.before_equity.text().strip()) if self.before_enabled.isChecked() else None
        trade.after = EquitySnapshot(time=self.after_time.dateTime().toMSecsSinceEpoch(), equity=self.after_equity.text().strip()) if self.after_enabled.isChecked() else None
        trade.notes = {key: edit.toPlainText().strip() for key, edit in self.notes.items()}
        trade.attachments = [item.text() for i in range(self.attachments.count()) if (item := self.attachments.item(i)) is not None]
        trade.chart_snapshots = list(self.snapshots)
        return trade

    def preview(self) -> None:
        try:
            result = calculate(self.value())
            quantity = f"{result.quantity:.10g}" if result.quantity is not None else "未知"
            realized = f"{result.realized:.10g}" if result.realized is not None else "暂无法计算"
            self.feedback.setText(f"剩余 {quantity} · 毛盈亏 {realized} USDT\n"
                                  + (f"净盈亏 {result.net:.10g} USDT" if result.net is not None else "净盈亏暂无法计算：" + "、".join(result.gaps)))
        except ValueError as exc:
            self.feedback.setText(str(exc))

    def save(self) -> None:
        try:
            trade = self.value()
            calculate(trade)
            self.ledger.scope(trade.book_id)
            similar = self.ledger.similar(trade)
            if similar and not confirm(self, "相似记录", "已有相同品种、方向、开仓时间和价格的记录。确认这是另一笔交易？"):
                return
            conversion = self.trade.version > 0 and self.trade.mode != trade.mode
            if conversion and not confirm(self, "核对转换", "确认已填写真实成交明细？程序将核对数量、盈亏和费用；原简录保留为历史版本。"):
                return
            self.trade = self.ledger.save(trade, allow_similar=similar, allow_conversion=conversion)
            self.snapshots = list(self.trade.chart_snapshots)
            self.clear_snapshot_draft()
            self.refresh_snapshots()
            self.book.setEnabled(False)
            self.detail_fills = deepcopy(self.trade.fills) if self.trade.mode == "detail" else self.detail_fills
            self.feedback.setText(f"已保存 · 版本 {self.trade.version} · 仅记账，未发送交易指令。")
            self.service.updated.emit("manual")
            self.saved.emit(self.trade.book_id, self.trade.id)
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))

    def load_trade(self, trade: ManualTrade) -> None:
        self.clear_snapshot_draft()
        self.trade = deepcopy(trade)
        self.reload_books(trade.book_id)
        self.book.setEnabled(trade.version == 0)
        select_data(self.product, trade.product)
        self.product_changed()
        self.symbol.setText(trade.symbol)
        select_data(self.direction, trade.direction)
        select_data(self.mode, trade.mode)
        self.mode_changed()
        self.detail_fills = deepcopy(trade.fills) if trade.mode == "detail" else []
        if trade.mode == "summary" and trade.fills:
            self.opening.load(trade.fills[0])
            self.open_external.setText(trade.fills[0].external_id)
            self.close_external.clear()
            self.closed.setChecked(len(trade.fills) > 1)
            if len(trade.fills) > 1:
                self.closing.load(trade.fills[1])
                self.close_external.setText(trade.fills[1].external_id)
        self.costs = deepcopy(trade.costs)
        self.fees_known.setChecked(trade.fees_known)
        self.funding_known.setChecked(trade.funding_known)
        for snapshot, enabled, edit, when in ((trade.before, self.before_enabled, self.before_equity, self.before_time),
                                               (trade.after, self.after_enabled, self.after_equity, self.after_time)):
            enabled.setChecked(snapshot is not None)
            edit.setText(snapshot.equity if snapshot else "")
            if snapshot:
                set_time(when, snapshot.time)
        for key, edit in self.notes.items():
            edit.setPlainText(trade.notes.get(key, ""))
        self.attachments.clear()
        self.attachments.addItems(trade.attachments)
        self.snapshots = list(trade.chart_snapshots)
        self.refresh_snapshots()
        self.refresh_tables()
        self.feedback.setText(f"编辑手动记录 · 版本 {trade.version}；保存保留历史版本。")

    def start_new(self, book_id: str = "") -> bool:
        # 复盘快捷入口不能把“补录”变成对上次已保存交易的覆盖。
        if (self.trade.version or self.opening.amount.text() or self.detail_fills or self.snapshots) and not confirm(
                self, "新建记账草稿", "新建会清空当前记账表单，已保存记录不受影响。继续？"):
            return False
        self.load_trade(ManualTrade(book_id=book_id, fills=[ManualFill(time=QDateTime.currentMSecsSinceEpoch())]))
        self.closed.setChecked(False)
        self.feedback.setText("新草稿 · 保存前不会改变账本。")
        return True

    def reset(self) -> None:
        self.start_new(str(self.book.currentData() or ""))


class AccountLedgerDialog(QDialog):
    def __init__(self, ledger: ManualLedger, book_id: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.ledger, self.book_id = ledger, book_id
        self.scope = ledger.scope(book_id)
        self.setWindowTitle("账户权益快照与转入转出 · USDT")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        self.snapshots = table(["时间", "账户总权益", "来源"])
        layout.addWidget(self.snapshots)
        layout.addWidget(button("更正选中权益快照", self.correct_snapshot))
        self.flows = table(["时间", "转入＋／转出－", "说明"])
        layout.addWidget(self.flows)
        form = QFormLayout()
        self.when, self.amount, self.memo = date_edit(), QLineEdit(), QLineEdit()
        form.addRow("流水时间", self.when)
        form.addRow("金额 USDT（转出填负数）", self.amount)
        form.addRow("说明", self.memo)
        layout.addLayout(form)
        actions = QHBoxLayout()
        actions.addWidget(button("记录流水", self.add))
        actions.addWidget(button("作废选中流水", self.remove))
        layout.addLayout(actions)
        self.begin, self.end = combo([]), combo([])
        choices = QFormLayout()
        choices.addRow("起始快照", self.begin)
        choices.addRow("结束快照", self.end)
        layout.addLayout(choices)
        self.complete = QCheckBox("我已核对所选期间全部转入转出（含无出入金的情况）")
        layout.addWidget(self.complete)
        layout.addWidget(button("计算账户权益变化", self.calculate_change))
        self.feedback = QLabel("仅展示账户权益变化，不作为单笔盈亏、账户收益率或完整回撤。")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.begin.currentIndexChanged.connect(lambda: self.complete.setChecked(False))
        self.end.currentIndexChanged.connect(lambda: self.complete.setChecked(False))
        self.refresh()

    def refresh(self) -> None:
        snapshots = sorted((obj(row) for _, row in self.ledger.store.list("manual_equity", self.scope)), key=lambda r: integer(r, "time"))
        fill_table(self.snapshots, [(str(integer(r, "time")), [timestamp(integer(r, "time") / 1000), text(r, "equity"), "手动录入"]) for r in snapshots])
        self.begin.clear()
        self.end.clear()
        for row in snapshots:
            for choice in (self.begin, self.end):
                choice.addItem(timestamp(integer(row, "time") / 1000), integer(row, "time"))
        self.end.setCurrentIndex(self.end.count() - 1)
        flows = self.ledger.store.list("manual_cashflow", self.scope)
        fill_table(self.flows, [(key, [timestamp(integer(obj(row), "time") / 1000), text(obj(row), "amount"), text(obj(row), "memo")]) for key, row in flows])
        self.complete.setChecked(False)

    def add(self) -> None:
        try:
            self.ledger.add_cashflow(self.book_id, self.when.dateTime().toMSecsSinceEpoch(), self.amount.text(), self.memo.text())
            self.refresh()
            self.feedback.setText("流水已保存；请重新核对期间完整性。")
        except ValueError as exc:
            self.feedback.setText(str(exc))

    def remove(self) -> None:
        key = selected_id(self.flows)
        if key and confirm(self, "作废流水", "作废后保留原记录，但不再参与权益调整。继续？"):
            self.ledger.void_cashflow(self.book_id, str(key))
            self.refresh()

    def correct_snapshot(self) -> None:
        key = selected_id(self.snapshots)
        if not key:
            return
        value, accepted = QInputDialog.getText(self, "更正账户权益", "新的总权益 USDT（所有引用此快照的交易同步更正并保留旧版）")
        if accepted:
            try:
                self.ledger.correct_snapshot(self.book_id, int(key), value)
                self.refresh()
            except ValueError as exc:
                self.feedback.setText(str(exc))

    def calculate_change(self) -> None:
        result = self.ledger.account_change(self.book_id, int(self.begin.currentData() or 0), int(self.end.currentData() or 0), complete=self.complete.isChecked())
        self.feedback.setText(f"账户权益变化：{result.get('change') if result.get('change') is not None else '暂无法计算'} USDT\n"
                              f"扣除净入金后：{result.get('adjusted') if result.get('adjusted') is not None else '暂无法计算'} USDT\n" + str(result["note"]))
