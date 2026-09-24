from contextlib import nullcontext
from pathlib import Path
import subprocess

import pytest

from coinpilot_ai import startup


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {"OtherApp": ("untouched.exe", self.REG_SZ)}
        self.fail_read = False
        self.fail_write = False
        self.writes = []

    def OpenKey(self, root, path, reserved, access):
        assert root == self.HKEY_CURRENT_USER and path == startup.RUN_KEY
        if self.fail_read and access == self.KEY_READ:
            raise PermissionError("读取被拒绝")
        return nullcontext(self)

    CreateKeyEx = OpenKey

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        if self.fail_write:
            raise PermissionError("写入被拒绝")
        self.writes.append((name, value))
        self.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        if self.fail_write:
            raise PermissionError("删除被拒绝")
        if name not in self.values:
            raise FileNotFoundError(name)
        self.writes.append((name, None))
        del self.values[name]


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setattr(startup, "startup_command", lambda *_: '"D:\\桌面 行情\\coinpilot-ai.exe"')
    return startup.StartupManager(tmp_path / "config.json", tmp_path / "icons", registry=FakeRegistry())


def test_enable_disable_and_idempotence_preserve_other_apps(manager):
    saved = []
    assert manager.read() is None
    manager.save_with(True, lambda: saved.append(True))
    assert "coinpilot-ai.exe" in manager.read()[0]
    manager.save_with(True, lambda: saved.append(True))
    assert len(manager.registry.writes) == 1
    manager.save_with(False, lambda: saved.append(False))
    manager.save_with(False, lambda: saved.append(False))
    assert manager.read() is None
    assert manager.registry.values == {"OtherApp": ("untouched.exe", 1)}
    assert saved == [True, True, False, False]


@pytest.mark.parametrize("previous, enabled", [(None, True), (("old.exe", 2), True), (("old.exe", 2), False)])
def test_config_failure_restores_exact_registry_entry(manager, previous, enabled):
    if previous is not None:
        manager.registry.values[startup.VALUE_NAME] = previous
    def fail():
        raise PermissionError("配置无法保存")
    with pytest.raises(PermissionError, match="配置无法保存"):
        manager.save_with(enabled, fail)
    assert manager.read() == previous


def test_registry_failure_does_not_save_configuration(manager):
    saved = []
    manager.registry.fail_write = True
    with pytest.raises(OSError, match="无法修改开机启动项"):
        manager.save_with(True, lambda: saved.append(True))
    assert not saved
    assert manager.read() is None
    manager.registry.fail_read = True
    with pytest.raises(OSError, match="无法读取开机启动项"):
        manager.save_with(False, lambda: saved.append(True))
    assert not saved


def test_rollback_failure_is_reported(manager):
    def fail():
        manager.registry.fail_write = True
        raise PermissionError("配置无法保存")
    with pytest.raises(OSError, match="回滚失败"):
        manager.save_with(True, fail)


def test_frozen_command_preserves_paths_and_excludes_one_time_flags(monkeypatch):
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", "D:/桌面 行情/coinpilot-ai.exe")
    monkeypatch.setattr(startup.sys, "argv", ["app", "--settings", "--quit-after", "10"])
    command = startup.startup_command("配置 文件.json", "缓存 目录")
    assert command == subprocess.list2cmdline([
        str(Path(startup.sys.executable).resolve()), "--config", str(Path("配置 文件.json").resolve()),
        "--cache-dir", str(Path("缓存 目录").resolve())])
    assert "--settings" not in command and "--quit-after" not in command


def test_source_command_uses_pythonw_and_absolute_script(monkeypatch, tmp_path):
    monkeypatch.setattr(startup.sys, "frozen", False, raising=False)
    monkeypatch.setattr(startup.sys, "executable", str(tmp_path / "python.exe"))
    (tmp_path / "pythonw.exe").touch()
    command = startup.startup_command("D:/config.json", "D:/icons")
    assert command == subprocess.list2cmdline([
        str(tmp_path / "pythonw.exe"), str(Path(startup.__file__).resolve().parent.parent / "coinpilot-ai.py"),
        "--config", str(Path("D:/config.json")), "--cache-dir", str(Path("D:/icons"))])
    (tmp_path / "pythonw.exe").unlink()
    with pytest.raises(OSError, match="pythonw.exe"):
        startup.startup_command("config.json", "icons")


def test_long_command_and_unsupported_platform_fail_explicitly(monkeypatch):
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    with pytest.raises(OSError, match="260"):
        startup.startup_command("x" * 270, "icons")
    monkeypatch.setattr(startup, "winreg", None)
    manager = startup.StartupManager("config.json", "icons")
    assert not manager.supported
    with pytest.raises(OSError, match="仅支持 Windows"):
        manager.read()
