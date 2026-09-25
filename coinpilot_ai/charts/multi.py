"""单工作区内的独立图窗与可选同步。"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .panel import ChartPanel
from coinpilot_ai.market.intervals import BARS


class MultiChart(QWidget):
    risk_requested = pyqtSignal(str, str, str)
    alert_requested = pyqtSignal(str, str, str)
    order_requested = pyqtSignal(str, str, str, str)
    SHAPES = {1: (1, 1), 2: (1, 2), 4: (2, 2), 6: (2, 3)}

    def __init__(self, service, primary, parent=None):
        super().__init__(parent)
        self.service = service
        self.panels = [primary]
        self.syncing = False
        record = service.store.get("chart_grid", "main", {})
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("图表"))
        self.layout_choice = QComboBox()
        for size in self.SHAPES:
            self.layout_choice.addItem(f"{size} 图", size)
        toolbar.addWidget(self.layout_choice)
        self.sync_options = {}
        for key, title in (("symbol", "同步品种"), ("interval", "同步周期"),
                           ("crosshair", "同步十字光标"), ("time", "同步时间"), ("zoom", "同步缩放")):
            check = QCheckBox(title)
            check.setChecked(bool(record.get("sync", {}).get(key, False)))
            check.toggled.connect(self.save)
            self.sync_options[key] = check
            toolbar.addWidget(check)
        toolbar.addStretch()
        root.addLayout(toolbar)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(3)
        root.addWidget(self.grid_host, 1)
        self.saved_pairs = record.get("pairs", [])
        primary.canvas.view_changed.connect(lambda: self.sync_view(primary))
        primary.canvas.crosshair_changed.connect(lambda stamp: self.sync_crosshair(primary, stamp))
        service.updated.connect(self.service_updated)
        service.chart_book.changed.connect(lambda kind, _key: self.refresh_indicator_pairs()
                                           if kind == "indicators" else None)
        self.layout_choice.currentIndexChanged.connect(self.layout_changed)
        size = record.get("size", 1)
        self.layout_choice.setCurrentIndex(max(0, self.layout_choice.findData(size)))
        self.layout_changed()

    @property
    def size(self):
        return self.layout_choice.currentData()

    def ensure_panels(self, count):
        defaults = ("1H", "4H", "5m", "1D", "15m")
        while len(self.panels) < count:
            index = len(self.panels)-1
            saved = self.saved_pairs[index] if index < len(self.saved_pairs) else None
            pair = (saved[0], saved[1]) if isinstance(saved, list) and len(saved) == 2 and saved[1] in BARS else (self.service.selected, defaults[index])
            panel = ChartPanel(self.service, local_pair=pair, local_key=f"pane{index+1}")
            panel.context_changed.connect(lambda *_, p=panel: self.panel_changed(p))
            panel.canvas.risk_requested.connect(self.risk_requested)
            panel.canvas.price_alert_requested.connect(self.alert_requested)
            panel.canvas.order_requested.connect(self.order_requested)
            panel.canvas.view_changed.connect(lambda p=panel: self.sync_view(p))
            panel.canvas.crosshair_changed.connect(lambda stamp, p=panel: self.sync_crosshair(p, stamp))
            self.panels.append(panel)

    def layout_changed(self, *_):
        size = self.size or 1
        self.ensure_panels(size)
        while self.grid.count():
            self.grid.takeAt(0)
        rows, columns = self.SHAPES[size]
        for index, panel in enumerate(self.panels):
            if index < size:
                panel.set_compact(size > 1)
                self.grid.addWidget(panel, index // columns, index % columns)
                panel.show()
            else:
                panel.hide()
        # takeAt 不会清除行列的伸展比例；收起多图后，空行列不能继续占用空间。
        for row in range(self.grid.rowCount()):
            self.grid.setRowStretch(row, 1 if row < rows else 0)
        for col in range(self.grid.columnCount()):
            self.grid.setColumnStretch(col, 1 if col < columns else 0)
        self.service.set_chart_pairs({panel.pair for panel in self.panels[1:size]})
        self.refresh_indicator_pairs()
        self.save()

    def panel_changed(self, source):
        if self.syncing:
            return
        if self.sync_options["symbol"].isChecked() or self.sync_options["interval"].isChecked():
            self.syncing = True
            try:
                if source is not self.panels[0]:
                    self.service.select(source.instrument if self.sync_options["symbol"].isChecked() else self.service.selected,
                                        source.bar if self.sync_options["interval"].isChecked() else self.service.bar)
                for panel in self.panels[1:self.size]:
                    if panel is source:
                        continue
                    inst = source.instrument if self.sync_options["symbol"].isChecked() else panel.instrument
                    bar = source.bar if self.sync_options["interval"].isChecked() else panel.bar
                    panel.set_local_pair(inst, bar)
            finally:
                self.syncing = False
        self.service.set_chart_pairs({panel.pair for panel in self.panels[1:self.size]})
        self.refresh_indicator_pairs()
        self.save()

    def refresh_indicator_pairs(self):
        wanted = set()
        for panel in self.panels[:self.size]:
            for item in panel.indicator_book.indicators:
                bar = item.get("bar", "chart")
                if item.get("visible", True) and bar != "chart" and bar != panel.bar:
                    wanted.add((panel.instrument, bar))
        self.service.set_indicator_pairs(wanted)

    def service_updated(self, kind):
        if kind == "selection" and not self.syncing and (self.sync_options["symbol"].isChecked() or self.sync_options["interval"].isChecked()):
            self.panel_changed(self.panels[0])
        if kind == "selection":
            self.refresh_indicator_pairs()
            self.save()

    def sync_view(self, source):
        if self.syncing or not (self.sync_options["time"].isChecked() or self.sync_options["zoom"].isChecked()):
            return
        self.syncing = True
        try:
            for target in self.panels[:self.size]:
                if target is source:
                    continue
                if self.sync_options["time"].isChecked():
                    target.canvas.left_time = source.canvas.left_time
                    target.canvas.follow = source.canvas.follow
                if self.sync_options["zoom"].isChecked():
                    span = source.canvas.count * source.canvas.interval
                    target.canvas.count = max(15., min(2000., span / target.canvas.interval))
                target.canvas._plot_revision += 1
                target.canvas.update()
                target.canvas.save_timer.start()
        finally:
            self.syncing = False

    def sync_crosshair(self, source, stamp):
        if not self.sync_options["crosshair"].isChecked():
            return
        for target in self.panels[:self.size]:
            if target is not source:
                target.canvas.external_crosshair_time = stamp
                target.canvas.update()

    def save(self, *_):
        self.service.store.put("chart_grid", "main", {"version": 1, "size": self.size or 1,
            "primary": [self.service.selected, self.service.bar],
            "pairs": [list(panel.pair) for panel in self.panels[1:]],
            "sync": {key: check.isChecked() for key, check in self.sync_options.items()}})

    def apply_record(self, record):
        primary = record.get("primary")
        if isinstance(primary, list) and len(primary) == 2 and primary[1] in BARS:
            self.service.select(*primary)
        self.saved_pairs = record.get("pairs", [])
        for key, check in self.sync_options.items():
            check.blockSignals(True)
            check.setChecked(bool(record.get("sync", {}).get(key, False)))
            check.blockSignals(False)
        size = record.get("size", 1)
        self.layout_choice.setCurrentIndex(max(0, self.layout_choice.findData(size)))
        self.ensure_panels(size)
        for panel, pair in zip(self.panels[1:], self.saved_pairs):
            if isinstance(pair, list) and len(pair) == 2 and pair[1] in BARS:
                panel.set_local_pair(*pair)
        self.layout_changed()
