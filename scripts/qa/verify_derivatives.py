"""使用临时库及模拟快照核对 OI/Funding 单点、短序列和无历史状态。"""
import os
from pathlib import Path
import sys
import tempfile
import time
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QFontDatabase

from coinpilot_ai.application.bootstrap import create_application
from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.charts.panel import ChartPanel
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.market.buffer import CandleBuffer


class EmptyVault:
    def read(self, key: str) -> None:
        return None


def main() -> None:
    app = create_application([])
    if sys.platform == "win32":
        QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"]) / "Fonts" / "msyh.ttc"))
    output = ROOT / "artifacts" / "derivatives"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coinpilot-derivatives-") as directory:
        service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), Path(directory) / "data.db",
                                 Path(directory) / "icons", vault=EmptyVault(), autostart=False)
        def no_network(*args: object, **kwargs: object) -> None:
            raise AssertionError("离线验收不得请求网络")
        service.api.get = no_network
        now = int(time.time())
        rows = CandleBuffer((now * 1000 + (i - 599) * 900000, 82800., 82900., 82700.,
                             82820., 10., 0., 0., 1) for i in range(600))
        service.candles[(service.selected, service.bar)] = rows
        panel = ChartPanel(service)
        try:
            panel.resize(1100, 900)
            panel.set_data(rows)
            panel.derivative_scroll.setFixedHeight(450)
            panel.derivative_button.click()
            service.derivatives.timer.stop()
            panel.show()
            app.processEvents()
            panel.set_data(rows)
            snapshots = {"funding": Decimal("0.0001"), "oi": Decimal("125000.25"),
                         "mark": Decimal("82791"), "index": Decimal("82820"), "basis": Decimal("-0.035016")}
            for kind, value in snapshots.items():
                service.derivatives.remember(service.selected, kind, now, value)
            panel.update_indicator_strips()
            assert panel.grab().save(str(output / "single-snapshot.png"))
            service.derivatives.history.clear()
            for offset in range(4):
                for kind, value in snapshots.items():
                    service.derivatives.remember(service.selected, kind, now - (3 - offset) * 60, value * (1 + Decimal(offset) / 10000))
            assert panel.grab().save(str(output / "short-samples.png"))
            assert panel.canvas.left_time is not None
            panel.canvas.left_time -= 7 * 86400000
            assert panel.grab().save(str(output / "no-history.png"))
            service.derivatives.errors[(service.selected, "funding")] = "公开数据请求失败"
            service.derivatives.history.pop((service.selected, "funding"))
            assert panel.grab().save(str(output / "request-failed.png"))
            print("单点、短序列、无历史和请求失败截图已生成；未请求网络。")
        finally:
            panel.close()
            service.close()
            panel.deleteLater()
            service.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    main()
