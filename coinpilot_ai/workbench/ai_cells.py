from collections.abc import Callable
import html
import re
from datetime import datetime
from typing import override

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from coinpilot_ai.ai.models import JsonValue
from coinpilot_ai.trading.models import OrderDraft
from coinpilot_ai.ui.qt import application, require
from coinpilot_ai.ui.theme import SPACING_SM, SPACING_XS


class _MessageBrowser(QTextBrowser):
    @override
    def loadResource(self, type: int, name: QUrl) -> None:
        # 同时阻断直接、引用式 Markdown 和 HTML 资源，不读取网络或本地文件。
        return None


class AiMessageCell(QWidget):
    def __init__(self, text: str, *, user: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.message_text = text
        self.render_count = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
        row = QHBoxLayout()
        title = QLabel("你" if user else "CoinPilot AI")
        title.setObjectName("sectionTitle")
        row.addWidget(title)
        row.addStretch()
        copy = QPushButton("复制")
        copy.setFixedWidth(48)
        copy.clicked.connect(lambda: require(application().clipboard()).setText(self.message_text))
        row.addWidget(copy)
        root.addLayout(row)
        self.browser = _MessageBrowser()
        self.browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        root.addWidget(self.browser)
        self.set_text(text, final=not user)

    def set_text(self, text: str, *, final: bool = False) -> None:
        self.message_text = text
        self.render_count += 1
        if final:
            # 模型不能通过 Markdown/HTML 加载用户本地文件或外部图片。
            safe = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"[图片：\1]", html.escape(text))
            self.browser.setMarkdown(safe)
        else:
            self.browser.setPlainText(text or "正在生成……")
        self._fit()

    def _fit(self) -> None:
        document = require(self.browser.document())
        document.setTextWidth(max(100, require(self.browser.viewport()).width()))
        self.browser.setFixedHeight(min(1200, max(28, int(document.size().height()) + 12)))

    @override
    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        self._fit()


TOOL_TITLES = {
    "get_market_snapshot": "读取行情快照", "get_candles": "获取已收盘 K 线", "get_indicators": "计算技术指标",
    "get_derivatives": "读取衍生品数据", "get_account_summary": "读取账户摘要", "get_positions": "读取仓位",
    "get_open_orders": "读取挂单", "get_alerts": "读取提醒", "get_trade_history": "读取交易历史",
    "get_trade_review": "读取交易复盘", "calculate_position_plan": "计算风险仓位", "create_order_draft": "创建订单草稿",
    "list_indicators": "查询指标目录", "get_indicator_definition": "读取指标定义",
    "validate_indicator_script": "编译指标代码", "create_indicator_draft": "生成指标草稿",
    "apply_indicator_to_chart": "准备图表指标草稿",
}


class AiToolCard(QWidget):
    def __init__(self, name: str, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tool_name = name
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(SPACING_SM, SPACING_XS, SPACING_SM, SPACING_XS)
        self.toggle = QPushButton("▸ " + TOOL_TITLES.get(name, name) + " · 执行中")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.expand)
        self.root.addWidget(self.toggle)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.hide()
        self.root.addWidget(self.detail)

    def expand(self, expanded: bool) -> None:
        self.detail.setVisible(expanded)

    def complete(self, result: dict[str, JsonValue]) -> None:
        ok = result.get("ok") is True
        self.toggle.setText("▸ " + TOOL_TITLES.get(self.tool_name, self.tool_name) + (" · ✓" if ok else " · 未完成"))
        warnings = result.get("warnings", [])
        freshness = {"live": "新鲜", "stale": "已过期", "historical": "历史数据", "replay": "回放", "missing": "缺少数据"}
        stamp = result.get("captured_at")
        captured = "未知"
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            try:
                captured = datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M:%S")
            except (ValueError, OverflowError, OSError):
                pass
        state = result.get("freshness")
        details = f"来源：{result.get('source', '未知')}\n状态：{freshness.get(state, '未知') if isinstance(state, str) else '未知'}\n获取时间：{captured}"
        data = result.get("data")
        if isinstance(data, dict):
            candles = data.get("candles")
            if isinstance(candles, list):
                details += f"\nK 线：{len(candles)} 根 · {data.get('bar', '')}"
            if "sample_count" in data:
                details += f"\n指标样本：{data['sample_count']} 根"
        if isinstance(warnings, list):
            details += "\n" + "\n".join(str(warning) for warning in warnings)
        self.detail.setText(details)


class AiOrderDraftCard(QWidget):
    def __init__(self, draft: OrderDraft, load: Callable[[OrderDraft], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.draft = draft
        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
        detail = QLabel(f"{draft.instrument} · {'做多' if draft.direction == 'long' else '做空'}\n"
                       f"入场：{draft.price or '市场价／待校验'}\n数量：{draft.size or '待补全'} 张\n"
                       f"止损：{draft.stop_loss or '待补全'}\n止盈：{draft.take_profit or '待补全'}\n"
                       f"{draft.reason}\n草稿尚未提交，载入后仍需校验与人工确认。")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        root.addWidget(detail)
        self.load_button = QPushButton("载入订单面板")
        self.load_button.clicked.connect(lambda: load(self.draft))
        root.addWidget(self.load_button)


class AiIndicatorDraftCard(QWidget):
    def __init__(self, data: dict[str, JsonValue], preview: Callable[[str], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        source = data.get("source")
        self.source = source if isinstance(source, str) and len(source.encode()) <= 256 * 1024 else ""
        root = QVBoxLayout(self)
        title = QLabel("指标代码草稿 · " + ("编译通过" if data.get("valid") is True else "需要修复"))
        title.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(title)
        detail = QLabel(str(data.get("description", "")) + "\n预览并确认后才能保存和添加到当前图表。")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        root.addWidget(detail)
        code = QPlainTextEdit(self.source)
        code.setReadOnly(True)
        code.setMaximumHeight(200)
        root.addWidget(code)
        diagnostics = data.get("diagnostics")
        if isinstance(diagnostics, list) and diagnostics:
            messages = [f"{row.get('line')}:{row.get('column')} · {row.get('code')} · {row.get('message')}"
                        for row in diagnostics if isinstance(row, dict)]
            report = QLabel("\n".join(messages))
            report.setTextFormat(Qt.TextFormat.PlainText)
            report.setWordWrap(True)
            root.addWidget(report)
        self.preview_button = QPushButton("预览／修改并添加到当前图表")
        self.preview_button.setEnabled(bool(self.source))
        self.preview_button.clicked.connect(lambda: preview(self.source))
        root.addWidget(self.preview_button)
