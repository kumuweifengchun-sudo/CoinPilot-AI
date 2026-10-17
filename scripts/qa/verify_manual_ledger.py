"""离线手动记账截图与刷新基准；临时账户、空凭据、禁止网络。"""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtTest import QTest
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QScrollArea
from coinpilot_ai.application.bootstrap import create_application
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.review.manual import EquitySnapshot, ManualCost, ManualFill, ManualTrade
from coinpilot_ai.review.manual_widgets import AccountLedgerDialog, FillDialog
from coinpilot_ai.review.page import ReviewPage
from coinpilot_ai.trading.panel import TradeController
from coinpilot_ai.ui.common import select_data
from coinpilot_ai.ui.theme import style_sheet
from coinpilot_ai.ui.qt import require


class EmptyVault:
    def read(self, key: str) -> None:
        return None


def main() -> None:
    app = create_application([])
    for name in ("msyh.ttc", "msyhbd.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"]) / "Fonts" / name))
    app.setStyleSheet(style_sheet())
    output = ROOT / "artifacts" / "manual-ledger"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), path / "manual.db", path / "icons",
                                 vault=EmptyVault(), autostart=False)
        with patch.object(service.transport, "request", side_effect=AssertionError("禁止联网")), \
             patch.object(service.api, "post", side_effect=AssertionError("禁止下单")):
            try:
                ledger = service.manual_ledger
                book = ledger.create_book("日常复盘", "示例交易所", "主账户")
                now = int(time.time() * 1000) - 3600000
                trade = ManualTrade(book_id=book, fees_known=True, funding_known=True,
                    fills=[ManualFill(time=now, price="50000", amount_mode="margin", amount="100", leverage="10"),
                           ManualFill(time=now + 1800000, action="close", price="51000", amount_mode="all")],
                    costs=[ManualCost(amount="1"), ManualCost("funding", "-0.2")],
                    before=EquitySnapshot(time=now - 1000, equity="1000"),
                    after=EquitySnapshot(time=now + 1800001, equity="2018.8"),
                    notes={"reason": "回踩确认后入场", "tags": "趋势, 回踩", "risk_budget": "10", "mistake": "按计划退出"})
                saved = ledger.save(trade)
                ledger.add_cashflow(book, now + 1000, "1000", "期间转入")
                controller = TradeController(service)
                controller.manual_entry.load_trade(saved)
                controller.entry_mode.setCurrentIndex(1)
                controller.sidebar.resize(400, 900)
                controller.sidebar.show()
                QTest.qWait(80)
                controller.sidebar.grab().save(str(output / "entry.png"))
                for area in controller.manual_entry.findChildren(QScrollArea):
                    bar = require(area.verticalScrollBar())
                    bar.setValue(bar.maximum())
                app.processEvents()
                controller.sidebar.grab().save(str(output / "entry-costs.png"))
                assert controller.sidebar.width() <= 440, controller.sidebar.width()
                page = ReviewPage(service)
                select_data(page.source, book)
                page.resize(1280, 900)
                page.show()
                QTest.qWait(80)
                page.grab().save(str(output / "review.png"))
                started = time.perf_counter()
                for _ in range(30):
                    page.refresh()
                elapsed = (time.perf_counter() - started) * 1000
                assert page.trade_table.rowCount() == 1
                dialog = AccountLedgerDialog(ledger, book, page)
                dialog.complete.setChecked(True)
                dialog.calculate_change()
                dialog.show()
                QTest.qWait(50)
                dialog.grab().save(str(output / "account.png"))
                detail = FillDialog("swap", saved.fills[-1], True, page)
                detail.show()
                QTest.qWait(50)
                detail.grab().save(str(output / "fill.png"))
                print(f"PASS: no credentials or network; 30 review refreshes {elapsed:.1f} ms; entry width {controller.sidebar.width()} px")
                detail.close()
                dialog.close()
                page.close()
                controller.sidebar.close()
                controller.bottom.close()
            finally:
                service.close()
                app.processEvents()


if __name__ == "__main__":
    main()
