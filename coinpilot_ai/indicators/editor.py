"""源码先编译预览，用户点击后才保存定义并添加实例。"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPaintEvent, QPainter, QPen
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from coinpilot_ai.charts.plots import draw_plot
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.ui.theme import color
from .catalog import IndicatorCatalog
from .models import IndicatorInstance, PlotStyle, SourceKind
from .runtime.engine import evaluate
from .runtime.result import IndicatorResult
from .script.compiler import compile_source
from .script.diagnostics import ScriptError
from .script.ir import Program

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService

EXAMPLE = '''//@version=6
indicator("EMA Cross", overlay=true)
fast = input.int(9, "Fast", minval=1, maxval=1000)
slow = input.int(21, "Slow", minval=1, maxval=1000)
a = ta.ema(close, fast)
b = ta.ema(close, slow)
buy = ta.crossover(a, b)
plot(a, title="Fast EMA", color=color.blue)
plot(b, title="Slow EMA", color=color.orange)
plotshape(buy, title="Buy", color=color.green)
'''


@dataclass(frozen=True)
class Preview:
    program: Program
    result: IndicatorResult | None
    rows: CandleBuffer
    bar: str
    instrument: str = ""


class ScriptPreview(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preview: Preview | None = None
        self.setMinimumHeight(160)

    @override
    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(color("chart_background")))
        preview = self.preview
        if preview is None or preview.result is None or not preview.rows:
            painter.setPen(QColor(color("text_secondary")))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "编译后使用已加载 K 线预览")
            return
        result = preview.result
        definition = preview.program.definition
        bounds = QRectF(8, 18, max(1., self.width() - 16.), max(1., self.height() - 26.))
        numbers = [value for output in definition.outputs if output.type != "bool"
                   for value in result.outputs[output.id] if value is not None]
        if definition.pane.mode == "overlay":
            numbers.extend(row[2] for row in preview.rows)
            numbers.extend(row[3] for row in preview.rows)
        if any(plot.type == "histogram" for plot in definition.plots):
            numbers.append(0.)
        minimum, maximum = (min(numbers), max(numbers)) if numbers else (0., 1.)
        if maximum == minimum:
            maximum, minimum = maximum + 1, minimum - 1
        size = len(result.times)
        def x_at(index: int) -> float:
            return bounds.left() + (index + .5) / size * bounds.width()
        def y_at(value: float) -> float:
            return bounds.bottom() - (value - minimum) / (maximum - minimum) * bounds.height()
        if definition.pane.mode == "overlay":
            painter.setPen(QPen(QColor(color("chart_axis")), .7))
            for index, row in enumerate(preview.rows):
                painter.drawLine(QPointF(x_at(index), y_at(row[2])), QPointF(x_at(index), y_at(row[3])))
        for plot in definition.plots:
            draw_plot(painter, plot, result.outputs[plot.output], result.times, start=0, end=size,
                      x_at=x_at, y_at=y_at, bounds=bounds, style=PlotStyle(color=plot.color, width=plot.width),
                      bar=preview.bar, bar_width=max(1., bounds.width() / size * .64))


class IndicatorEditor(QDialog):
    def __init__(self, service: "CockpitService", apply: Callable[[IndicatorInstance], None],
                 parent: QWidget | None = None, *, source: str = EXAMPLE, identity: str | None = None,
                 language: SourceKind = "pine", apply_title: str = "添加到图表",
                 preview_pair: tuple[str, str] | None = None,
                 preview_rows: Callable[[], CandleBuffer] | None = None,
                 can_apply: Callable[[], bool] | None = None) -> None:
        super().__init__(parent)
        self.service, self.apply_instance = service, apply
        self.identity = identity
        self.language: SourceKind = language
        self.preview_pair, self.preview_rows = preview_pair, preview_rows
        self.can_apply = can_apply or (lambda: True)
        self.expected_revision = service.indicator_catalog.resolve(identity).revision if identity else None
        self.generation = 0
        self.job: int | None = None
        self.last_preview: Preview | None = None
        self.saved_program: Program | None = None
        self.saved_source = ""
        self.preview_scope = service.scope
        self.setWindowTitle("Pine 基础指标导入／脚本编辑")
        self.resize(860, 780)
        root = QVBoxLayout(self)
        note = QLabel("导入自己有权使用的源码。支持 Pine v5/v6 基础指标子集；不支持策略、跨周期请求或复杂绘图对象。")
        note.setWordWrap(True)
        root.addWidget(note)
        self.code = QPlainTextEdit(source)
        self.code.setTabStopDistance(28)
        root.addWidget(self.code, 2)
        self.feedback = QLabel("先编译并查看兼容报告，保存与添加均由用户点击触发。")
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        self.preview = ScriptPreview()
        root.addWidget(self.preview, 1)
        actions = QHBoxLayout()
        self.compile_button = QPushButton("编译并预览")
        self.compile_button.clicked.connect(self.compile_preview)
        self.save_button = QPushButton("保存指标")
        self.save_button.clicked.connect(self.save_definition)
        self.add_button = QPushButton(apply_title)
        self.add_button.clicked.connect(self.add_to_chart)
        for button in (self.compile_button, self.save_button, self.add_button):
            actions.addWidget(button)
        root.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        root.addWidget(close)
        self.code.textChanged.connect(self.source_changed)
        self.source_changed()

    def source_changed(self) -> None:
        self.generation += 1
        if self.job is not None:
            self.service.compute.cancel(self.job)
            self.job = None
        self.last_preview = None
        self.preview.preview = None
        self.preview.update()
        self.save_button.setEnabled(False)
        self.add_button.setEnabled(False)
        self.feedback.setText("源码已修改，请编译并查看预览。")

    def compile_preview(self) -> None:
        self.generation += 1
        generation, source, scope = self.generation, self.code.toPlainText(), self.service.scope
        self.preview_scope = scope
        if self.job is not None:
            self.service.compute.cancel(self.job)
        self.save_button.setEnabled(False)
        self.add_button.setEnabled(False)
        self.feedback.setText("正在编译及预览…")
        identity = self.identity or "custom.preview"
        revision = self.expected_revision + 1 if self.expected_revision is not None else 1
        instrument, bar = self.preview_pair or (self.service.selected, self.service.bar)
        rows = (CandleBuffer(self.preview_rows()[-500:]) if self.preview_rows is not None else
                CandleBuffer(self.service.candles.get((instrument, bar), ())[-500:]))
        def operation() -> Preview:
            program = compile_source(source, identity=identity, revision=revision, language=self.language)
            catalog = IndicatorCatalog()
            catalog.register_program(program)
            instance = IndicatorInstance("preview", identity, revision)
            result = evaluate(instance, rows, bar=bar, catalog=catalog) if rows else None
            return Preview(program, result, rows, bar, instrument)
        def ready(value: object, error: object) -> None:
            if generation != self.generation:
                return
            self.job = None
            if self.service.closed or self.service.scope != scope:
                self.feedback.setText("环境已变化，请重新编译预览。")
                return
            if isinstance(error, ScriptError):
                diagnostic = error.diagnostic
                self.feedback.setText(f"第 {diagnostic.line} 行，第 {diagnostic.column} 列 · {diagnostic.code}\n{diagnostic.message}")
            elif error or not isinstance(value, Preview):
                self.feedback.setText("编译或预览失败，请检查源码及资源限制。")
            else:
                self.last_preview = value
                self.preview.preview = value
                self.preview.update()
                definition = value.program.definition
                self.feedback.setText(f"兼容：已支持的 v1 子集 · {len(definition.inputs)} 项输入 · {len(definition.plots)} 个绘图\n"
                                      f"{value.instrument} · {value.bar} · {len(value.rows)} 根 K 线预览；信号仅使用已收盘数据。")
                self.save_button.setEnabled(True)
                self.add_button.setEnabled(True)
        self.job = self.service.compute.submit(operation, ready)

    def _save(self) -> Program:
        if self.last_preview is None or self.service.scope != self.preview_scope or not self.can_apply():
            raise ValueError("源码或环境已变化，请重新编译")
        source = self.code.toPlainText()
        if self.saved_program is not None and self.saved_source == source:
            return self.saved_program
        program = self.service.indicator_library.save(source, language=self.language, identity=self.identity,
            expected_revision=self.expected_revision, confirmed=True)
        self.identity, self.expected_revision = program.definition.id, program.definition.revision
        self.saved_program, self.saved_source = program, source
        self.service.updated.emit("indicator_definitions")
        return program

    def save_definition(self) -> None:
        try:
            program = self._save()
            self.feedback.setText(f"已保存 {program.definition.name} · revision {program.definition.revision}；已有实例继续使用原版本。")
        except ValueError as exc:
            self.feedback.setText(str(exc))

    def add_to_chart(self) -> None:
        from uuid import uuid4
        try:
            if not self.can_apply():
                raise ValueError("图表或会话已变化，请重新打开预览")
            program = self._save()
            self.apply_instance(IndicatorInstance(uuid4().hex, program.definition.id, program.definition.revision))
            self.service.indicator_library.mark_used(program.definition.id)
            self.feedback.setText("已加入指标；参数和逐输出样式可继续编辑。")
        except ValueError as exc:
            self.feedback.setText(str(exc))

    @override
    def reject(self) -> None:
        self.generation += 1
        if self.job is not None:
            self.service.compute.cancel(self.job)
            self.job = None
        super().reject()
