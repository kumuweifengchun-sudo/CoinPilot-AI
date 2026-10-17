import time
from typing import override

from PyQt6.QtCore import QEvent, QObject

from coinpilot_ai.ai.context import ChartContext, QuoteContext, WorkspaceSnapshot
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.charts.multi import MultiChart
from coinpilot_ai.charts.panel import ChartPanel


class WorkbenchAiContext(QObject):
    """记录最近操作的图窗；发送时只提取工作区纯数据，不传 300 根 K 线或账户。"""

    def __init__(self, service: CockpitService, multi: MultiChart, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.service, self.multi = service, multi
        self.active_panel: ChartPanel = multi.panels[0]
        self._bound: set[ChartPanel] = set()
        multi.layout_choice.currentIndexChanged.connect(self.bind)
        self.bind()

    def bind(self, *_args: object) -> None:
        for panel in self.multi.panels:
            if panel not in self._bound:
                panel.canvas.installEventFilter(self)
                self._bound.add(panel)

    @override
    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a1 is not None and a1.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress):
            for panel in self.multi.panels:
                if a0 is panel.canvas:
                    self.active_panel = panel
                    break
        return super().eventFilter(a0, a1)

    def panel(self) -> ChartPanel:
        return self.active_panel if self.active_panel in self.multi.panels[:self.multi.panel_count] else self.multi.panels[0]

    def capture(self) -> WorkspaceSnapshot:
        chat = self.service.ai_chat
        if chat is not None and chat.scope.startswith("manual:"):
            return WorkspaceSnapshot(time.time(), chat.scope, "manual")
        panel = self.panel()
        canvas = panel.canvas
        start, end = canvas.visible()
        begin = float(canvas.times[start]) / 1000 if start < len(canvas.times) else None
        finish = float(canvas.times[end - 1]) / 1000 if end > start else None
        cursor = None
        if canvas.pointer is not None:
            cursor = canvas.drawing_anchor(canvas.pointer)[0] / 1000
        elif canvas.external_crosshair_time is not None:
            cursor = float(canvas.external_crosshair_time) / 1000
        now = time.time()
        row = self.service.quotes.get(panel.instrument)
        quote = None
        if row:
            try:
                quote = QuoteContext(str(row["price"]), float(row.get("time", 0)), str(row.get("source", "unknown")),
                                     -5 <= now - float(row.get("time", 0)) <= 30)
            except (ValueError, TypeError, KeyError):
                pass
        return WorkspaceSnapshot(now, self.service.scope, self.service.environment,
                                 ChartContext(panel.instrument, panel.bar, begin, finish, cursor), quote)
