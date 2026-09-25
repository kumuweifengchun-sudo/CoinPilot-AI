from pathlib import Path
import runpy
import subprocess
import sys

from coinpilot_ai import version


def test_packaged_version_reads_bundled_metadata(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.3"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    metadata = runpy.run_path(version.__file__)
    assert metadata["VERSION"] == "0.2.3"
    assert metadata["WINDOWS_VERSION"] == (0, 2, 3, 0)


def test_cli_and_qt_report_project_version(app, tmp_path):
    entry = Path(__file__).resolve().parent.parent / "coinpilot-ai.py"
    result = subprocess.run([sys.executable, str(entry), "--version"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == f"CoinPilot AI {version.VERSION}"
    assert app.applicationVersion() == version.VERSION
    assert not list(tmp_path.iterdir())
