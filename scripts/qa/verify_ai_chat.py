"""离线聊天截图和节流测量；临时业务库、空凭据、模拟模型，不访问真实账户。"""

import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest

from coinpilot_ai.ai.models import Item, JsonValue, TurnStatus
from coinpilot_ai.ai.providers.base import ModelRequest, ProviderCallback, ProviderCompleted, ProviderConfig, ProviderEvent, TextDelta, ToolCall, ToolRequested
from coinpilot_ai.ai.transport import RequestHandle
from coinpilot_ai.application.bootstrap import create_application
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.market.buffer import CandleBuffer
from coinpilot_ai.market.intervals import floor_time, shift
from coinpilot_ai.ui.qt import require
from coinpilot_ai.workbench.ai_cells import AiMessageCell, AiToolCard
from coinpilot_ai.workbench.window import Workbench


class EmptyVault:
    def read(self, key: str) -> None:
        return None


class OfflineRunner:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.callbacks: list[ProviderCallback] = []

    def start(self, config: ProviderConfig, request: ModelRequest, callback: ProviderCallback, *, timeout_ms: int) -> RequestHandle:
        self.requests.append(request)
        self.callbacks.append(callback)
        return RequestHandle("offline", lambda: None)

    def emit(self, event: ProviderEvent) -> None:
        self.callbacks[-1](event)


def main() -> None:
    app = create_application([])
    if sys.platform == "win32":
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"]) / "Fonts" / name))
    output = ROOT / "artifacts" / "ai-chat"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coinpilot-ai-qa-") as directory:
        path = Path(directory)
        service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), path / "business.sqlite3", path / "icons",
                                 vault=EmptyVault(), autostart=False)
        service.refresh_market = lambda: None
        service.fetch_candles = lambda inst, bar: None
        service.refresh_account = lambda: None
        runner = OfflineRunner()
        chat = service.ai_chat
        assert chat is not None
        chat.manager = runner
        service.store.put("ai_service", "offline", {"id": "offline", "name": "离线验收", "protocol": "chat",
                                                   "base_url": "http://localhost", "model": "模拟模型"})
        inst, bar = "BTC-USDT-SWAP", "1H"
        end = shift(floor_time(int(time.time() * 1000), bar), bar, -1)
        service.candles[(inst, bar)] = CandleBuffer((shift(end, bar, i - 29), 100.0, 110.0, 90.0, 101.0,
                                                   1.0, 0.0, 0.0, 1) for i in range(30))
        service.quotes[inst] = {"price": "101", "time": time.time(), "source": "offline"}
        window = Workbench(service)
        window.resize(1440, 960)
        window.show()
        QTest.qWait(80)
        panel = window.ai_chat
        try:
            panel.pin_chart()
            panel.input.setPlainText("检查 BTC 1H 的趋势，再给我一个可编辑的限价订单草稿。")
            panel.send()
            runner.emit(TextDelta("我会先检查已收盘 K 线和指标，再准备草稿。"))
            for call in (
                ToolCall("candles", "get_candles", {"instrument": inst, "bar": bar, "limit": 30}),
                ToolCall("indicators", "get_indicators", {"instrument": inst, "bar": bar, "limit": 30, "kind": "EMA", "params": {"period": 20}}),
                ToolCall("draft", "create_order_draft", {"instrument": inst, "action": "open", "direction": "long", "order_type": "limit",
                                                        "margin": "cross", "size": "1", "price": "100", "stop_loss": "90", "take_profit": "120", "reason": "离线验收示例"}),
            ):
                runner.emit(ToolRequested(call))
            runner.emit(ProviderCompleted())
            deadline = time.monotonic() + 5
            while len(runner.requests) < 2 and time.monotonic() < deadline:
                QTest.qWait(10)
            assert len(runner.requests) == 2
            runner.emit(TextDelta("### 分析\n\n已检查 **30 根已收盘 K 线**与 EMA20。\n\n- 数值来自离线示例。\n- 草稿只表达计划，载入后仍须校验和人工确认。"))
            runner.emit(ProviderCompleted())
            QTest.qWait(80)
            scroll = require(panel.message_scroll.verticalScrollBar())
            scroll.setValue(0)
            app.processEvents()
            assert window.grab().save(str(output / "chat-tools.png"))
            for cell in panel._cells.values():
                if isinstance(cell, AiToolCard):
                    cell.toggle.setChecked(True)
                    break
            QTest.qWait(40)
            assert panel.grab().save(str(output / "tool-expanded.png"))
            scroll.setValue(scroll.maximum())
            QTest.qWait(40)
            assert panel.grab().save(str(output / "order-draft.png"))
            assert not service.trading.prepared and not service.store.list("local_order", service.scope)

            chat.new_conversation("offline", "模拟模型")
            panel.input.setPlainText("测量流式刷新")
            panel.send()
            cell = next(cell for cell in panel._cells.values() if isinstance(cell, AiMessageCell) and cell.message_text == "")
            original = chat.store.update_item
            checkpoints = [0]
            def measured(item_id: str, *, scope: str, text: str | None = None,
                         payload: dict[str, JsonValue] | None = None, status: TurnStatus | None = None) -> Item:
                if status is None and text is not None:
                    checkpoints[0] += 1
                return original(item_id, scope=scope, text=text, payload=payload, status=status)
            chat.store.update_item = measured
            updates = cell.render_count
            deltas = [0]
            ticker = QTimer()
            ticker.setInterval(2)
            def delta() -> None:
                deltas[0] += 1
                runner.emit(TextDelta("文本 "))
            ticker.timeout.connect(delta)
            started = time.perf_counter()
            ticker.start()
            QTest.qWait(1200)
            ticker.stop()
            elapsed = time.perf_counter() - started
            renders = cell.render_count - updates
            stream_checkpoints, stream_deltas = checkpoints[0], deltas[0]
            assert renders / elapsed <= 15 and stream_checkpoints / elapsed <= 2
            before_hidden = cell.render_count
            window.workspace.set_visible("ai", False)
            ticker.start()
            QTest.qWait(300)
            ticker.stop()
            assert cell.render_count == before_hidden and chat.running
            chat.store.update_item = original
            runner.emit(ProviderCompleted())
            window.workspace.set_visible("ai", True)
            dock = window.workspace.docks["ai"]
            panel.input.setPlainText("浮动后继续追问")
            dock.setFloating(True)
            QTest.qWait(80)
            assert dock.grab().save(str(output / "floating-chat.png"))
            assert panel.input.toPlainText() == "浮动后继续追问"
            report = (f"offline simulated provider; no real account/order requests\n"
                      f"elapsed_seconds={elapsed:.3f}\ndeltas={stream_deltas}\n"
                      f"renders={renders}; renders_per_second={renders / elapsed:.2f}\n"
                      f"checkpoints={stream_checkpoints}; checkpoints_per_second={stream_checkpoints / elapsed:.2f}\n"
                      "hidden_continuous_renders=0\norder_submission=0\n")
            (output / "metrics.txt").write_text(report, encoding="utf-8")
            print(report)
            dock.setFloating(False)
            window.pages.setCurrentIndex(2)
            window.settings_page.select_section("AI 服务")
            window.settings_page.chat_advanced_button.setChecked(True)
            QTest.qWait(80)
            assert window.grab().save(str(output / "provider-compatibility.png"))
        finally:
            window.exiting = True
            window.close()
            service.close()
            window.deleteLater()
            service.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
