"""资源路径在更换工作目录和冻结运行时仍指向正确位置。"""
from pathlib import Path
import sys

from coinpilot_ai.core.paths import resource_path


def test_source_resources_do_not_depend_on_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    project = Path(__file__).resolve().parents[2]
    icon = resource_path("coinpilot_ai/assets/app_icon/app.ico")
    assert icon == project / "coinpilot_ai/assets/app_icon/app.ico"
    assert icon.is_file()


def test_frozen_resources_use_bundle_root(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_path("coinpilot_ai/assets/update-install.ps1") == tmp_path / "coinpilot_ai/assets/update-install.ps1"
