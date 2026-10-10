"""公开衍生品快照的刷新、失效隔离与稀疏图形可见性。"""
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

from coinpilot_ai.application.service import CockpitService
from coinpilot_ai.charts.panel import ChartPanel
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.integrations.transport import ApiError
from coinpilot_ai.ui.theme import color

type Request = tuple[str, Callable[[object, ApiError | None], None]]


class EmptyVault:
    def read(self, key: str) -> None:
        return None


@pytest.fixture
def offline(app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[CockpitService, list[Request]]]:
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path / "derivatives.db",
                             tmp_path / "icons", vault=EmptyVault(), autostart=False)
    requests: list[Request] = []

    def get(path: str, callback: Callable[[object, ApiError | None], None],
            params: dict[str, str] | None = None, private: bool = False) -> None:
        assert not private
        requests.append((path, callback))

    monkeypatch.setattr(service.api, "get", get)
    yield service, requests
    service.close()
    service.deleteLater()
    app.processEvents()


def test_open_requests_snapshots_and_does_not_duplicate_pending(offline: tuple[CockpitService, list[Request]]) -> None:
    service, requests = offline
    panel = ChartPanel(service)
    try:
        panel.derivative_button.click()
        monitor = service.derivatives
        assert len(monitor.queue) == 4
        monitor.step()
        assert requests[-1][0] == "/api/v5/public/funding-rate"
        panel.derivative_button.click()
        panel.derivative_button.click()
        assert len(monitor.queue) == 3
        requests[-1][1]([{"fundingRate": "0", "ts": str(int(time.time() * 1000))}], None)
        assert monitor.values[service.selected]["funding"]["value"] == 0
        assert not monitor.pending
    finally:
        panel.deleteLater()


def test_error_is_visible_and_reset_rejects_old_callbacks(offline: tuple[CockpitService, list[Request]]) -> None:
    service, requests = offline
    monitor = service.derivatives
    monitor.schedule({service.selected})
    monitor.step()
    callback = requests[-1][1]
    callback(None, ApiError("离线失败"))
    assert monitor.errors[(service.selected, "funding")] == "公开数据请求失败"
    monitor.received(service.selected, "funding", [{"fundingRate": "0.0001"}], None, monitor.generation)
    assert (service.selected, "funding") not in monitor.errors
    monitor.reset()
    callback([{"fundingRate": "0.01"}], None)
    assert not monitor.values and not monitor.history and not monitor.errors


def test_basis_does_not_mix_stale_index_with_new_mark(offline: tuple[CockpitService, list[Request]]) -> None:
    service, _ = offline
    monitor = service.derivatives
    inst = service.selected
    monitor.values[inst] = {"index": {"value": Decimal("100"), "time": time.time() - 180, "source": "okx"}}
    monitor.received(inst, "mark", [{"markPx": "99"}], None, monitor.generation)
    assert "basis" not in monitor.values[inst]
    monitor.received(inst, "index", [{"idxPx": "100"}], None, monitor.generation)
    assert monitor.values[inst]["basis"]["value"] == -1


def test_single_snapshot_has_readout_and_visible_dot(offline: tuple[CockpitService, list[Request]]) -> None:
    service, _ = offline
    panel = ChartPanel(service)
    try:
        now = int(time.time())
        panel.canvas.resize(900, 400)
        panel.canvas.set_data([[now * 1000, 100., 110., 90., 100., 1., 0., 0., 1]])
        strip = panel.derivative_strips[0]
        strip.resize(900, 88)
        service.derivatives.remember(service.selected, "funding", now, Decimal("0.0001"))
        assert "最新 0.0100%" in strip.summary()
        image = strip.grab().toImage()
        bounds = strip.plot_bounds()
        assert panel.canvas.left_time is not None
        x = bounds.left() + (now * 1000 - panel.canvas.left_time) / (panel.canvas.count * panel.canvas.interval) * bounds.width()
        pixel = image.pixelColor(round(x * image.devicePixelRatio()), round(bounds.center().y() * image.devicePixelRatio()))
        assert pixel == QColor(color("focus"))
        # 只有历史采样且已过期时不能仍标为最新。
        service.derivatives.history[(service.selected, "funding")].clear()
        service.derivatives.remember(service.selected, "funding", now - 180, Decimal("0"))
        assert "过期 0%" in strip.summary()
    finally:
        panel.deleteLater()
